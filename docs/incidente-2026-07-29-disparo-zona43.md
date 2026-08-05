# Incidente 2026-07-29 13:37 — disparo da zona 43 alertado como "Sem disparos"

**Sintoma relatado:** o alarme disparou e o alerta chegou dizendo que o último evento era
"nenhum evento", enquanto o app Intelbras Guardian mostrava corretamente o disparo na zona 43.

**Horário real:** 13:37 (o relato de "01:38" era 13:38 em 12h). Todos os horários abaixo em UTC
(local = UTC−3).

## Timeline (histórico + logbook + trace, tudo do HA)

| UTC | Evento | Fonte |
|-----|--------|-------|
| 16:37:19.591 | `binary_sensor.piscina_cross_line_alarm` → `on` (IVA da CAM 4 PISCINA) | logbook |
| 16:37:19.653973 | `alarm_control_panel.casa_casa_2` → **`triggered`** | logbook |
| 16:37:19.654244 | automação `Seguranca - Alarme disparou` inicia | trace |
| 16:37:19.655992 | push despachado para `notify.oneplus15` | logbook |
| 16:37:19.669940 | `binary_sensor.casa_zona_43` → `on` | logbook |
| 16:37:19.678195 | `sensor.casa_ultimo_disparo` → **`Zona 43`** (24 ms tarde demais) | logbook |
| 16:37:23.046 | `sensor.casa_last_event` → `Disparo de Setor` / zona `CAM 4 PISCINA` (nuvem, ~3,4 s) | logbook |
| 16:37:55.152 | `Restauração de Disparo de Setor` | logbook |
| 16:37:57.116 | painel → `disarmed` | logbook |

Mensagem efetivamente renderizada, extraída do trace da automação (run
`68eb98306a0ec146ee58ae8d952f1dec`):

```
ALARME DISPAROU! Ultimo evento: Sem disparos
```

## Causa raiz

A automação dispara em `to: triggered` do `alarm_control_panel` e monta a mensagem com
`{{ states('sensor.casa_ultimo_disparo') }}` — **duas entidades diferentes**.

As duas são alimentadas pelo MESMO ciclo do coordinator (o dicionário já continha
`_last_trigger = {550793: {zone_name: "Zona 43", ...}}` quando o painel virou `triggered`), mas
cada entidade escreve seu estado quando o listener dela roda. `alarm_control_panel` é a primeira
plataforma de `const.PLATFORMS`, então escreve — e aciona a automação — **24 ms antes** de o
sensor escrever a zona. A automação leu o valor anterior do sensor: `Sem disparos` (valor de
partida desde o último restart, já que não havia disparo anterior registrado).

Não houve falha de leitura, de parse nem da nuvem: a zona foi identificada corretamente no
mesmo instante do disparo. Era só uma corrida de ordem de escrita entre entidades.

Nota: `sensor.casa_last_event` (nuvem) é outra coisa — chega ~3,4 s depois e mostra a zona pelo
nome amigável da nuvem (`CAM 4 PISCINA`, que é a mesma zona 43). Ele não serve para alerta de
disparo, por ser "o último evento de qualquer tipo" (é sobrescrito por "Teste periódico" etc.).

## Correção aplicada (repo)

A zona passa a viajar nos atributos do **próprio painel**, atômica com o estado `triggered`:

- `state_logic.build_last_trigger_attrs()` (puro, testável sem HA) devolve
  `last_trigger_zone`, `last_trigger_zones`, `last_trigger_time` e `last_trigger_is_current`.
- `alarm_control_panel.py`: as duas classes de painel (por partição e unificada) expõem esses
  atributos.
- `coordinator.py`: passa a registrar `_trigger_started_at` (transição False→True do disparo,
  tanto pelo caminho de poll quanto pelo SSE) e a carimbar `captured_at` em cada registro de
  `_last_trigger`. É o que permite distinguir "zona do disparo em curso" de um registro
  remanescente de um disparo anterior (`last_trigger_is_current`).
- `tests/integration/test_last_trigger_attrs.py`: 5 testes de regressão (poll com zona em
  alarme, sem disparo, disparo já encerrado, SSE sem zona, SSE com zona).

## Pendente — deploy e automação

O Raspberry Pi roda uma versão ANTERIOR da integração (não tem `state_logic.py` nem
`models.py`), então a correção **ainda não está em produção**. Depois do deploy (e do restart do
HA, obrigatório para custom component), a automação `Seguranca - Alarme disparou`
(`automations.yaml`, id `1771210001005`) deve passar a ler o atributo:

```yaml
- action: notify.mobile_app_oneplus15
  data:
    title: ALARME
    message: >-
      ALARME DISPAROU! Zona: {{ trigger.to_state.attributes.last_trigger_zone
      if trigger.to_state.attributes.get('last_trigger_is_current')
      else states('sensor.casa_ultimo_disparo') }}
    data:
      priority: high
      ttl: 0
```

(o mesmo template no `shell_command.send_wpp_notify`, lembrando a regra do WhatsApp: uma linha
só, sem acento e sem aspas duplas).
