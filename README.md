# PDV Intelbras iMHDX

Pacote de integracao entre WRPDV/Sierra e gravador Intelbras iMHDX.

## Conteudo

- `docs/MANUAL_PDV_INTELBRAS_IMHDX.md`: manual operacional completo.
- `scripts/pdv_intelbras_bridge.py`: servico Python instalado nos PDVs Linux.
- `scripts/install_bridge_pdv.sh`: instalador generico da ponte no PDV.
- `services/*.service`: unidades systemd usadas nos PDVs.

## Fluxo

```text
PDV Linux -> iMHDX 192.168.24.227:38801
```

Cada PDV usa porta UDP propria:

```text
PDV1=52101, PDV2=52102, ..., PDV12=52112
```

## Status em 2026-05-24

- Acesso SSH confirmado nos PDVs 1 a 12 com usuario administrativo informado pelo operador.
- `pdv-intelbras-bridge.service` reiniciado nos PDVs 1 a 12 sem reiniciar os computadores.
- PDV6 estava inativo e voltou para `active`.
- `pdv_intelbras_bridge.py` atualizado nos 12 PDVs com correcao para trocar automaticamente de arquivo na virada de dia.
- Backup criado em cada PDV:
  ` /opt/pdv-intelbras-bridge/pdv_intelbras_bridge.py.bak_20260524_1043`

## Modo de producao

O sistema funciona em envio direto. Cada PDV envia UDP para o iMHDX usando sua
propria porta de origem (`52100 + numero do PDV`). No iMHDX, cada entrada POS
deve estar configurada com o IP real do PDV e a porta UDP correspondente.

## Observacao de seguranca

Este pacote nao deve conter senhas, tokens ou dumps completos com credenciais.
Antes de publicar no GitHub, revise qualquer arquivo novo adicionado fora desta pasta.
