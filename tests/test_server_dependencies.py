"""Catch Flask/Werkzeug incompatibility before building the viewer image."""
import unittest


class ServerDependencyTests(unittest.TestCase):
    def test_flask_can_import_and_serve(self):
        from flask import Flask
        app = Flask(__name__)

        @app.route("/")
        def index():
            return "ok"

        with app.test_client() as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b"ok")
