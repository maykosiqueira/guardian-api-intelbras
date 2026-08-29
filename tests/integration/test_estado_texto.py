"""The panel carries its state in the integration's own wording.

The card reads "Armado ausente", because that is how Home Assistant names
`armed_away` for every alarm brand there is - correct, and unhelpful on a panel
whose owner only ever arms it whole. Core state names have no per-entity
override, so the wording travels as an attribute that the tile card can show
through `state_content`.
"""
import pytest

from custom_components.intelbras_guardian.alarm_control_panel import _estado_texto


@pytest.mark.parametrize(
    "state,esperado",
    [
        ("disarmed", "Desarmado"),
        ("armed_away", "Armado"),
        ("armed_home", "Armado parcial"),
        ("arming", "Armando"),
        ("triggered", "DISPARADO"),
    ],
)
def test_estados_conhecidos(state, esperado):
    assert _estado_texto(state) == esperado


def test_sem_estado():
    assert _estado_texto(None) == "Desconhecido"


def test_estado_desconhecido_passa_direto():
    # Um estado novo do core aparece cru em vez de sumir do card.
    assert _estado_texto("armed_custom_bypass") == "armed_custom_bypass"
