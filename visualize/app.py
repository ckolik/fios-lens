import json
from pathlib import Path
from typing import Any, Dict, List

from flask import Flask, abort, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder="static",
        template_folder="templates",
    )

    @app.route("/")
    def home():
        return render_template("index.html")

    @app.route("/api/files", methods=["GET"])
    def list_files():
        files: List[Dict[str, Any]] = []
        for path in sorted(OUTPUT_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue

            if isinstance(data, dict):
                files.append(
                    {
                        "name": path.name,
                        "run_id": data.get("run_id", path.stem),
                        "collected_at": data.get("collected_at"),
                        "device_count": data.get("device_count") or len(data.get("devices", [])),
                    }
                )
            elif isinstance(data, list):
                files.append(
                    {
                        "name": path.name,
                        "run_id": path.stem,
                        "collected_at": None,
                        "device_count": len(data),
                    }
                )
        return jsonify({"files": files})

    @app.route("/api/files/<path:filename>", methods=["GET"])
    def get_file(filename: str):
        safe_name = Path(filename).name
        target = OUTPUT_DIR / safe_name
        if target.suffix != ".json":
            abort(400, description="Only .json files are allowed.")
        if not target.exists() or not target.is_file():
            abort(404, description="Snapshot not found.")
        try:
            data_text = target.read_text(encoding="utf-8")
            data = json.loads(data_text)
        except Exception:
            abort(500, description="Failed to read snapshot.")

        return jsonify(data)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
