"""E2E tests for deployer static file serving."""


async def test_index_page_loads(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "CodeTalk 控制中心" in resp.text


async def test_deploy_page_loads(client):
    resp = await client.get("/deploy.html")
    assert resp.status_code == 200
    assert "CodeTalk 部署系统" in resp.text
    assert "DeepWiki" not in resp.text
    assert "deepwiki" not in resp.text.lower()


async def test_start_page_loads(client):
    resp = await client.get("/start.html")
    assert resp.status_code == 200
    assert "CodeTalk 服务启动" in resp.text
    assert "DeepWiki" not in resp.text
    assert "deepwiki" not in resp.text.lower()


async def test_style_css_served(client):
    resp = await client.get("/style.css")
    assert resp.status_code == 200
    assert "css" in resp.headers.get("content-type", "")


async def test_static_background_avoids_heavy_infinite_orb_animation(client):
    resp = await client.get("/style.css")
    assert resp.status_code == 200
    css = resp.text
    assert "animation: orb-float" not in css
    assert "@keyframes orb-float" not in css
    assert "blur(80px)" not in css


async def test_app_js_served(client):
    resp = await client.get("/app.js")
    assert resp.status_code == 200
    ct = resp.headers.get("content-type", "")
    assert "javascript" in ct or "text/" in ct
    assert "deepwiki" not in resp.text.lower()


async def test_start_app_js_has_no_deepwiki_service(client):
    resp = await client.get("/start-app.js")
    assert resp.status_code == 200
    assert "deepwiki" not in resp.text.lower()


async def test_nonexistent_file_returns_404(client):
    resp = await client.get("/does-not-exist.html")
    assert resp.status_code == 404
