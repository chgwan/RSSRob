import web.webapp as webapp


def test_about_page_renders():
    r = webapp.app.test_client().get("/about")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Chenguang Wan" in html
    assert "testing" in html.lower()
    assert "research notification" in html.lower()
