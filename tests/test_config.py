
import pytest
import yaml

from context_engine.config import Config, load_config, resolve_ollama_url


def test_default_config():
    config = Config()
    assert config.compression_level == "standard"
    assert config.embedding_model == "BAAI/bge-small-en-v1.5"
    assert config.retrieval_top_k == 20
    assert config.indexer_watch is True


def test_load_from_yaml(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump({
        "compression": {"level": "full", "model": "phi3:mini"},
        "retrieval": {"top_k": 50},
    }))
    config = load_config(global_path=config_file)
    assert config.compression_level == "full"
    assert config.compression_model == "phi3:mini"
    assert config.retrieval_top_k == 50


def test_project_override(tmp_path):
    global_file = tmp_path / "config.yaml"
    global_file.write_text(yaml.dump({
        "compression": {"level": "standard"},
        "indexer": {"ignore": [".git"]},
    }))
    project_file = tmp_path / ".context-engine.yaml"
    project_file.write_text(yaml.dump({
        "compression": {"level": "full"},
        "indexer": {"ignore": [".git", "dist"]},
    }))
    config = load_config(global_path=global_file, project_path=project_file)
    assert config.compression_level == "full"
    assert "dist" in config.indexer_ignore


def test_resource_profile_auto_detect():
    config = Config()
    profile = config.detect_resource_profile()
    assert profile in ("light", "standard", "full")


def test_ollama_url_default():
    assert Config().ollama_url == "http://localhost:11434"


def test_ollama_url_yaml_override(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump({
        "compression": {"ollama_url": "http://nas.local:11434"},
    }))
    config = load_config(global_path=config_file)
    assert config.ollama_url == "http://nas.local:11434"


def test_resolve_ollama_url_prefers_env_var(monkeypatch):
    config = Config(ollama_url="http://nas.local:11434")
    monkeypatch.setenv("CCE_OLLAMA_URL", "http://other.host:9999")
    assert resolve_ollama_url(config) == "http://other.host:9999"


def test_resolve_ollama_url_falls_back_to_config(monkeypatch):
    config = Config(ollama_url="http://nas.local:11434")
    monkeypatch.delenv("CCE_OLLAMA_URL", raising=False)
    assert resolve_ollama_url(config) == "http://nas.local:11434"


def test_resolve_ollama_url_ignores_blank_env_var(monkeypatch):
    config = Config(ollama_url="http://nas.local:11434")
    monkeypatch.setenv("CCE_OLLAMA_URL", "   ")
    assert resolve_ollama_url(config) == "http://nas.local:11434"


def test_ollama_url_yaml_type_validation(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump({
        "compression": {"ollama_url": 12345},
    }))
    with pytest.raises(ValueError, match="ollama_url"):
        load_config(global_path=config_file)


def test_marginal_ratio_config_mapping(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("retrieval:\n  marginal_ratio: 0.7\n")
    from context_engine.config import load_config
    config = load_config(global_path=cfg_file)
    assert config.retrieval_marginal_ratio == 0.7


def test_marginal_ratio_default():
    from context_engine.config import Config
    assert Config().retrieval_marginal_ratio == 0.75


def test_serve_config_defaults():
    config = Config()
    assert config.serve_idle_timeout_minutes == 30
    assert config.serve_max_ort_threads == 2


def test_serve_config_yaml_mapping(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("serve:\n  idle_timeout_minutes: 60\n  max_ort_threads: 4\n")
    config = load_config(global_path=cfg_file)
    assert config.serve_idle_timeout_minutes == 60
    assert config.serve_max_ort_threads == 4


def test_serve_config_type_validation(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("serve:\n  idle_timeout_minutes: not_a_number\n")
    with pytest.raises(ValueError, match="serve.idle_timeout_minutes"):
        load_config(global_path=cfg_file)
