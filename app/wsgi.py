from app.config import assert_secure_config, load
from app.database import Database
from app.dashboard.server import create_app

settings = load()
assert_secure_config(settings.path)
db = Database(settings.data_dir / "security-camera.db")
db.initialize()
app = create_app(settings, db)
