import pytest

from app import db


@pytest.fixture()
def session():
    db.init_db("sqlite:///:memory:")
    s = db.get_session()
    yield s
    s.close()
