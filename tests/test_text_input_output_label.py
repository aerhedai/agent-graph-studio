from __future__ import annotations

from backend.nodes.text_input import TextInputConfig
from backend.nodes.text_output import TextOutputConfig


def test_text_input_config_label_defaults_to_none():
    config = TextInputConfig(value="hi")
    assert config.label is None


def test_text_input_config_accepts_explicit_label():
    config = TextInputConfig(value="hi", label="customer_message")
    assert config.label == "customer_message"


def test_text_output_config_label_defaults_to_none():
    config = TextOutputConfig()
    assert config.label is None


def test_text_output_config_accepts_explicit_label():
    config = TextOutputConfig(label="reply")
    assert config.label == "reply"
