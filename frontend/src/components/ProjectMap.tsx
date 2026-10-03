import type { ProjectInfo } from "@/lib/api";

/** The THP / project an application falls in, with its map documents. */
export function ProjectMap({ project }: { project: ProjectInfo }) {
  const label = `${project.kind ?? "Project"} ${project.identifier}`;
  const images = project.documents.filter((d) => d.content_type.startsWith("image/"));
  const files = project.documents.filter((d) => !d.content_type.startsWith("image/"));
  const sources = [...new Set(project.documents.map((d) => d.source).filter(Boolean))];
  return (
    <section className="project-map">
      <h3>
        {label}
        {project.name && <span className="muted"> · {project.name}</span>}
      </h3>
      {images.map((doc) => (
        <a key={doc.id} href={doc.url} target="_blank" rel="noopener" className="project-map-image">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={doc.url} alt={`${label} map`} loading="lazy" />
        </a>
      ))}
      {files.length > 0 && (
        <ul className="project-map-files">
          {files.map((doc) => (
            <li key={doc.id}>
              <a href={doc.url} target="_blank" rel="noopener">
                {doc.filename.toLowerCase().endsWith(".pdf") ? "Open the project map (PDF)" : doc.filename}
              </a>
            </li>
          ))}
        </ul>
      )}
      {project.documents.length === 0 && project.has_boundary && (
        <p className="small muted">The project boundary is drawn on the map above.</p>
      )}
      {sources.length > 0 && <p className="small muted">Source: {sources.join("; ")}.</p>}
    </section>
  );
}
