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
    css_resp = await client.get("/style.css")
    deploy_resp = await client.get("/deploy.html")
    assert css_resp.status_code == 200
    assert deploy_resp.status_code == 200
    css = css_resp.text
    assert "animation: orb-float" not in css
    assert "@keyframes orb-float" not in css
    assert "nebula-orb" not in css
    assert "nebula-orb" not in deploy_resp.text
    assert "filter: blur(36px)" not in css
    assert "blur(80px)" not in css


async def test_deployer_static_avoids_continuous_decorative_animations(client):
    css_resp = await client.get("/style.css")
    start_resp = await client.get("/start.html")
    assert css_resp.status_code == 200
    assert start_resp.status_code == 200

    decorative_animation_tokens = [
        "animation: deploy-pulse-ring",
        "animation: deploy-flow",
        "animation: pulse-aura",
    ]
    combined = css_resp.text + "\n" + start_resp.text
    for token in decorative_animation_tokens:
        assert token not in combined

    assert "animation: spin" in css_resp.text
    assert "animation: svc-spin" in start_resp.text


async def test_app_js_served(client):
    resp = await client.get("/app.js")
    assert resp.status_code == 200
    ct = resp.headers.get("content-type", "")
    assert "javascript" in ct or "text/" in ct
    assert "deepwiki" not in resp.text.lower()
    assert "function errorDetailMessage(detail, fallback)" in resp.text
    assert "showServiceActionMessage('error'" in resp.text
    assert "[object Object]" not in resp.text


async def test_start_app_js_has_no_deepwiki_service(client):
    resp = await client.get("/start-app.js")
    assert resp.status_code == 200
    assert "deepwiki" not in resp.text.lower()


async def test_start_app_js_renders_structured_service_errors(client):
    resp = await client.get("/start-app.js")
    assert resp.status_code == 200
    assert "function errorDetailMessage(detail, fallback)" in resp.text
    assert "errorDetailMessage(err.detail, 'HTTP ' + res.status)" in resp.text
    assert "[object Object]" not in resp.text


async def test_nonexistent_file_returns_404(client):
    resp = await client.get("/does-not-exist.html")
    assert resp.status_code == 404
