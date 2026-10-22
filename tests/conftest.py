import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.settings as settings
    import app.db as database
    import app.main as main
    import app.data.importer as importer
    import app.core.security as security
    import app.core.prompt_manager as prompt_manager
    import app.services.images as images
    import app.services.runs as runs
    data = tmp_path/'data'
    config = tmp_path/'settings.yaml'
    config.write_bytes((settings.ROOT/'config/settings.yaml').read_bytes())
    for module in (settings,database,main,importer,security,prompt_manager,images,runs):
        monkeypatch.setattr(module,'HOME',data,raising=False)
    monkeypatch.setattr(settings,'CONFIG',config)
    monkeypatch.setattr(main,'CONFIG',config)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as value:
        yield value


@pytest.fixture
def imported(client):
    if not list(Path(__file__).resolve().parent.parent.glob('selected_240_six_groups*.zip')):
        pytest.skip('私有 Benchmark QA 包未提供；公开仓库仅运行不依赖该包的测试')
    response = client.post('/api/dataset/import-bundled')
    assert response.status_code==200,response.text
    return client
