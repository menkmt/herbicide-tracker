import httpx

from app.providers.enrichment.website import WebsiteFinder, candidate_domains

SITES = {
    "wmbeaty.com": (
        "<html><head><title>W.M. Beaty &amp; Associates | Forest Management</title></head>"
        "<body><a href='/contact-us'>Contact</a> Redding, California</body></html>"
    ),
    "wmbeaty.com/contact-us": (
        "<html><body>Office: <a href='tel:530-243-2783'>530.243.2783</a> "
        "<a href='mailto:info@wmbeaty.com'>info@wmbeaty.com</a> "
        "staff@gmail.com</body></html>"
    ),
    "westernhelicopter.com": (
        "<html><head><title>Western Helicopter Services</title></head>"
        "<body>Call (503) 222-0000 info@westernhelicopter.com</body></html>"
    ),
}


def fake_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.host.removeprefix("www.") + request.url.path.rstrip("/")
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if key in SITES:
            return httpx.Response(200, html=SITES[key])
        raise httpx.ConnectError("no such host", request=request)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_site_showing_the_permit_phone_is_verified():
    f = WebsiteFinder(client=fake_client()).find(
        "WM BEATY AND ASSOC.", known_phone=["(530) 336-6986", "(530) 243-2783"])
    assert f.status == "verified"
    assert f.website.startswith("https://wmbeaty.com")
    assert f.phone == "(530) 243-2783"
    assert f.email == "info@wmbeaty.com"       # gmail address dropped as personal
    assert "number on the county permit" in f.evidence


def test_right_name_wrong_phone_is_held_for_review():
    f = WebsiteFinder(client=fake_client()).find(
        "WESTERN HELICOPTER SERVICES", known_phone="(503) 538-9469")
    assert f.status == "likely"
    assert "538-9469" in f.evidence


def test_nothing_found_is_said_plainly():
    f = WebsiteFinder(client=fake_client()).find("PP Forestry LLC", known_phone="(541) 821-3875")
    assert f.status == "not_found" and f.website is None
    assert "https://ppforestry.com" in f.tried


def test_no_generic_single_word_domains():
    for name in ("WESTERN HELICOPTER SERVICES", "SIERRA PACIFIC INDUSTRIES", "FOREST PROTECTION"):
        domains = candidate_domains(name)
        assert not any(d.split(".")[0] in ("western", "sierra", "protection") for d in domains)
