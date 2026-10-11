import json
import re

from upstream.export import export_artifact, export_static


def test_static_export(tmp_path, scenario_file):
    out = export_static(tmp_path / "site", scenario_file)
    html = (out / "index.html").read_text()
    assert 'data-mode="static"' in html
    for f in ("app.js", "style.css", "card.svg", "vendor/leaflet/leaflet.js", "vendor/leaflet/leaflet.css",
              "data/scenario.json", ".nojekyll"):
        assert (out / f).exists(), f
    data = json.loads((out / "data" / "scenario.json").read_text())
    assert data["frames"] and data["truth"]


def test_artifact_export_is_self_contained(tmp_path, scenario_file):
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps({"runs": 3, "rows": [1, 2, 3], "gis": {}}))
    page = export_artifact(tmp_path / "a.html", scenario_file, ev).read_text()
    assert not page.lstrip().lower().startswith("<!doctype")
    assert "<html" not in page and "<body>" not in page and "<head>" not in page
    assert page.startswith("<title>")
    assert 'data-mode="artifact"' in page
    srcs = re.findall(r'<script[^>]*\ssrc="([^"]+)"', page)
    assert srcs == ["https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"]
    assert 'integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="' in page
    hrefs = re.findall(r'<link[^>]*href="([^"]+)"', page)
    assert all(h.startswith("https://fonts.googleapis.com") for h in hrefs)
    m = re.search(r'<script type="application/json" id="scenario-data">(.*?)</script>', page, re.S)
    data = json.loads(m.group(1))
    assert data["evaluation"]["runs"] == 3 and "rows" not in data["evaluation"]
    assert 'src="data:image/svg+xml;base64,' in page
    # no file downloads and no server-only features in the artifact
    assert "download" not in page.lower().replace("downloads", "")
    assert "/api/" not in page.split('id="scenario-data"')[0]
    assert 'id="tab-live"' not in page and 'id="tab-replay"' in page and 'id="tab-card"' in page


def test_cloud_export_points_at_the_api(tmp_path, scenario_file):
    plain = export_static(tmp_path / "plain", scenario_file)
    assert (plain / "config.js").read_text().strip() == "window.UPSTREAM_CONFIG = {};"
    assert not (plain / "report.html").exists()
    out = export_static(tmp_path / "cloud", scenario_file, api_base="https://abc.execute-api.ap-south-1.amazonaws.com/")
    assert '"apiBase": "https://abc.execute-api.ap-south-1.amazonaws.com"' in (out / "config.js").read_text()
    report = (out / "report.html").read_text()
    assert "/api/presign" in report and 'src="config.js"' in report
    assert 'id="tab-incidents"' in (out / "index.html").read_text()


def test_static_export_ships_the_pwa(tmp_path, scenario_file):
    out = export_static(tmp_path / "site", scenario_file)
    for f in ("backend.js", "pwa.js", "sw.js", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png",
              "icons/icon-maskable-512.png", "icons/icon.svg"):
        assert (out / f).exists(), f
    man = json.loads((out / "manifest.webmanifest").read_text())
    assert man["display"] == "standalone" and man["start_url"].startswith("./")
    assert {"192x192", "512x512"} <= {i["sizes"] for i in man["icons"]}
    html = (out / "index.html").read_text()
    assert 'rel="manifest"' in html and 'id="public-view"' in html
    # backend.js must run after config.js and before app.js
    assert html.index('src="config.js"') < html.index('src="backend.js"') < html.index('src="app.js"') < html.index('src="pwa.js"')
    sw = (out / "sw.js").read_text()
    assert "SKIP_WAITING" in sw and "'/api/'" in sw


def test_artifact_has_no_pwa_shell(tmp_path, scenario_file):
    page = export_artifact(tmp_path / "a.html", scenario_file).read_text()
    assert "<!-- PWA -->" not in page and 'id="public-view"' not in page
    assert 'rel="manifest"' not in page and 'src="backend.js"' not in page and 'src="pwa.js"' not in page
