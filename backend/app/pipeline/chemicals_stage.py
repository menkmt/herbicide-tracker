"""Pipeline stage: identify products, and flag the chemicals that matter.

Runs after records are stored.  It resolves every reported product to a
registration and its active ingredients, derives the warning flags from the
sources that justify them, and attaches a flag summary to each application so
the public grid row can show a warning without a second query.

The California restricted-material flags come from the county's own permits,
which the importer has already parsed.  That means the tracker can state
"2,4-D is a California restricted material" and cite the permit that says so,
rather than relying on an external list it has not verified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chemicals.flags import (
    ChemicalFlag,
    FlagLevel,
    FlagSet,
    flags_from_permit_materials,
    flags_from_watchlist,
)
from app.chemicals.resolver import (
    ProductResolver,
    ResolvedProduct,
    Verification,
    load_watchlist,
)
from app.core.provenance import ExtractionMethod, Provenance, SourceType
from app.models import (
    ActiveIngredient,
    ApplicationCluster,
    ChemicalFlagRow,
    ClusterRecord,
    Permit,
    PermitMaterial,
    Product,
    ProductIngredient,
    PurProduct,
    PurRecord,
)


@dataclass
class ChemicalStageResult:
    products_resolved: int = 0
    products_unresolved: int = 0
    products_seed_only: int = 0
    ingredients_created: int = 0
    flags_created: int = 0
    alerts: int = 0
    unresolved_names: list[str] = field(default_factory=list)


def _get_or_create_ingredient(session: Session, name: str) -> ActiveIngredient:
    slug = slugify(name)
    ingredient = session.scalar(select(ActiveIngredient).where(ActiveIngredient.slug == slug))
    if ingredient is None:
        ingredient = ActiveIngredient(name=name, slug=slug)
        session.add(ingredient)
        session.flush()
    return ingredient


def _persist_product(session: Session, resolved: ResolvedProduct) -> Product | None:
    if not resolved.base_epa_reg_no:
        return None
    row = session.scalar(
        select(Product).where(Product.base_epa_reg_no == resolved.base_epa_reg_no)
    )
    if row is None:
        row = Product(base_epa_reg_no=resolved.base_epa_reg_no)
        session.add(row)
    # Never downgrade: a verified lookup must not be overwritten by a seed.
    if row.verification != Verification.VERIFIED or resolved.verification == Verification.VERIFIED:
        row.name = resolved.name or row.name
        row.slug = slugify(resolved.name) if resolved.name else row.slug
        row.registrant = resolved.registrant or row.registrant
        row.formulation = resolved.formulation or row.formulation
        row.signal_word = resolved.signal_word or row.signal_word
        row.density_lb_per_gallon = (
            resolved.density_lb_per_gallon or row.density_lb_per_gallon
        )
        row.is_adjuvant = resolved.is_adjuvant
        row.federal_restricted_use = (
            resolved.federal_restricted_use
            if resolved.federal_restricted_use is not None
            else row.federal_restricted_use
        )
        row.verification = resolved.verification
        row.resolved_at = datetime.now(UTC)
    session.flush()
    return row


def run(
    session: Session,
    *,
    record_ids: list[int],
    resolver: ProductResolver | None = None,
) -> ChemicalStageResult:
    """Resolve and flag the chemicals used by a batch of records."""
    resolver = resolver or ProductResolver()
    result = ChemicalStageResult()
    watchlist = load_watchlist()

    if not record_ids:
        return result

    lines = session.scalars(
        select(PurProduct).where(PurProduct.record_id.in_(record_ids))
    ).all()

    resolved_by_reg: dict[str, ResolvedProduct] = {}
    product_row_by_reg: dict[str, Product] = {}

    for line in lines:
        resolved = resolver.resolve(line.base_epa_reg_no, line.product_name)
        key = line.base_epa_reg_no or (line.product_name or "")
        if key and key not in resolved_by_reg:
            resolved_by_reg[key] = resolved
            if resolved.verification == Verification.UNRESOLVED:
                result.products_unresolved += 1
                result.unresolved_names.append(line.product_name or key)
            else:
                result.products_resolved += 1
                if resolved.verification == Verification.SEED:
                    result.products_seed_only += 1

        row = _persist_product(session, resolved)
        if row is not None:
            line.product_id = row.id
            if resolved.base_epa_reg_no:
                product_row_by_reg[resolved.base_epa_reg_no] = row

    # Link products to their active ingredients.
    for reg, resolved in resolved_by_reg.items():
        row = product_row_by_reg.get(reg)
        if row is None:
            continue
        for ingredient_ref in resolved.ingredients:
            ingredient = _get_or_create_ingredient(session, ingredient_ref.name)
            exists = session.scalar(
                select(ProductIngredient).where(
                    ProductIngredient.product_id == row.id,
                    ProductIngredient.ingredient_id == ingredient.id,
                )
            )
            if exists is None:
                session.add(
                    ProductIngredient(
                        product_id=row.id,
                        ingredient_id=ingredient.id,
                        percent=ingredient_ref.percent,
                    )
                )
                result.ingredients_created += 1

    # --- flags ------------------------------------------------------------
    flag_set = FlagSet()

    # California restricted materials, stated by the counties' own permits.
    for permit in session.scalars(select(Permit)).all():
        materials = session.scalars(
            select(PermitMaterial).where(PermitMaterial.permit_id == permit.id)
        ).all()
        if not materials:
            continue
        provenance = Provenance(
            source_type=SourceType.RESTRICTED_MATERIALS_PERMIT,
            source_name="County restricted materials permit",
            source_id=permit.permit_number,
            extraction_method=ExtractionMethod.DOCX_TABLE,
        )
        flag_set.extend(
            flags_from_permit_materials(
                [
                    {
                        "number": m.number,
                        "name": m.name,
                        "methods": m.methods,
                        "form": m.form,
                    }
                    for m in materials
                ],
                provenance,
            )
        )

    # Protect Lassen's own watchlist, applied to every chemical the tracker
    # knows about by name. That means the active ingredients identified in use
    # records *and* the materials named on county permits: a watchlisted
    # chemical that a county has authorised should be flagged as watchlisted
    # even before a use report for it arrives, and flagging it in one place but
    # not the other would look arbitrary on the public site.
    subjects: list[str] = [
        i.name for i in session.scalars(select(ActiveIngredient)).all()
    ]
    for material in session.scalars(select(PermitMaterial)).all():
        if material.name and material.name not in subjects:
            subjects.append(material.name)

    watch_provenance = Provenance(
        source_type=SourceType.WATCHLIST,
        source_name="Protect Lassen watchlist",
        extraction_method=ExtractionMethod.HUMAN,
    )
    flag_set.extend(flags_from_watchlist(subjects, watchlist, watch_provenance))

    _persist_flags(session, flag_set, result)
    _mark_ingredients(session, flag_set)
    _attach_cluster_flags(session, record_ids, flag_set, result)
    return result


def _persist_flags(session: Session, flag_set: FlagSet, result: ChemicalStageResult) -> None:
    for flag in flag_set.flags:
        subject_type, subject_id = _resolve_subject(session, flag)
        exists = session.scalar(
            select(ChemicalFlagRow).where(
                ChemicalFlagRow.subject_name == flag.subject,
                ChemicalFlagRow.reason == flag.reason,
            )
        )
        if exists is not None:
            continue
        session.add(
            ChemicalFlagRow(
                subject_type=subject_type,
                subject_id=subject_id,
                subject_name=flag.subject,
                level=flag.level,
                reason=flag.reason,
                label=flag.label,
                detail=flag.detail,
                is_regulatory=flag.is_regulatory,
                source_type=flag.provenance.source_type,
                source_name=flag.provenance.describe(),
                source_url=flag.provenance.source_url,
                confidence=flag.provenance.confidence,
            )
        )
        result.flags_created += 1
    session.flush()


def _resolve_subject(session: Session, flag: ChemicalFlag) -> tuple[str, int]:
    ingredient = session.scalar(
        select(ActiveIngredient).where(ActiveIngredient.slug == slugify(flag.subject))
    )
    if ingredient is not None:
        return "ingredient", ingredient.id
    return "name", 0


def _mark_ingredients(session: Session, flag_set: FlagSet) -> None:
    """Set the quick-filter booleans used by the public chemical index."""
    for flag in flag_set.flags:
        ingredient = session.scalar(
            select(ActiveIngredient).where(ActiveIngredient.slug == slugify(flag.subject))
        )
        if ingredient is None:
            continue
        if flag.is_regulatory and flag.level == FlagLevel.RED:
            ingredient.is_california_restricted = True
        elif not flag.is_regulatory:
            ingredient.is_watchlisted = True
    session.flush()


def _attach_cluster_flags(
    session: Session,
    record_ids: list[int],
    flag_set: FlagSet,
    result: ChemicalStageResult,
) -> None:
    """Summarise each application's flags onto its cluster row."""
    by_subject: dict[str, list[ChemicalFlag]] = {}
    for flag in flag_set.flags:
        by_subject.setdefault(flag.subject.upper(), []).append(flag)

    cluster_ids = {
        row.cluster_id
        for row in session.scalars(
            select(ClusterRecord).where(ClusterRecord.record_id.in_(record_ids))
        ).all()
    }

    for cluster_id in cluster_ids:
        cluster = session.get(ApplicationCluster, cluster_id)
        if cluster is None:
            continue
        member_ids = [
            row.record_id
            for row in session.scalars(
                select(ClusterRecord).where(ClusterRecord.cluster_id == cluster_id)
            ).all()
        ]
        product_lines = session.scalars(
            select(PurProduct).where(PurProduct.record_id.in_(member_ids))
        ).all()

        cluster_flags = FlagSet()
        for line in product_lines:
            names = {(line.product_name or "").upper()}
            if line.product_id:
                product = session.get(Product, line.product_id)
                if product is not None:
                    for link in product.ingredients:
                        ingredient = session.get(ActiveIngredient, link.ingredient_id)
                        if ingredient is not None:
                            names.add(ingredient.name.upper())
            for name in names:
                for flag in by_subject.get(name, []):
                    cluster_flags.add(flag)

        cluster.flags = cluster_flags.to_dict()
        if cluster_flags.has_red:
            result.alerts += 1
    session.flush()


def records_for_batch(session: Session, batch_source_ids: list[int]) -> list[int]:
    return [
        row.id
        for row in session.scalars(
            select(PurRecord).where(PurRecord.source_file_id.in_(batch_source_ids))
        ).all()
    ]
