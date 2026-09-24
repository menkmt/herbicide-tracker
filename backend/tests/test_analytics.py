from datetime import date

from app.api.analytics import clean_path, is_bot, referrer_host, visitor_hash


def test_bots_are_excluded():
    assert is_bot("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)")
    assert is_bot("curl/8.4.0")
    assert is_bot(None)
    assert is_bot("")
    assert not is_bot(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
    )


def test_visitor_hash_rotates_daily_and_needs_the_secret():
    ip, ua = "203.0.113.7", "Mozilla/5.0"
    a = visitor_hash(ip, ua, date(2026, 9, 24), "s")
    assert a == visitor_hash(ip, ua, date(2026, 9, 24), "s")
    assert a != visitor_hash(ip, ua, date(2026, 9, 25), "s")
    assert a != visitor_hash(ip, ua, date(2026, 9, 24), "other-secret")
    assert a != visitor_hash("203.0.113.8", ua, date(2026, 9, 24), "s")
    assert len(a) == 32 and ip not in a


def test_referrer_keeps_only_the_host_and_drops_self():
    own = "herbicidetracker.com"
    assert referrer_host("https://www.google.com/search?q=hexazinone", own) == "google.com"
    assert referrer_host("https://herbicidetracker.com/applications", own) is None
    assert referrer_host("https://www.herbicidetracker.com/", own) is None
    assert referrer_host("", own) is None
    assert referrer_host("not a url", own) is None


def test_path_drops_query_so_searched_addresses_are_not_kept():
    assert clean_path("/near-me?address=175+Russell+Ave&miles=1") == "/near-me"
    assert clean_path("/application/x#sources") == "/application/x"
    assert clean_path("applications") == "/applications"
