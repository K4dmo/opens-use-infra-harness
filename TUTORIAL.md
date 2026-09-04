# Tutorial: como usar o OpenSUSE infra harness

Este guia é para quem nunca instalou um programa desse tipo. Você não precisa saber programar. Precisa de um servidor **openSUSE**, acesso de administrador (`root`) e cerca de 30 minutos.

O resultado esperado: a cada 15 minutos o servidor olha a própria saúde, pede ajuda a uma inteligência artificial (quando necessário) e manda um resumo no **Discord**.

---

## 1. O que isso faz, em linguagem simples

Imagine um plantonista que, de 15 em 15 minutos:

1. Anota o nome da máquina, uso de memória, disco, carga, serviços que falharam e atualizações pendentes.
2. Mostra esse relatório para uma IA (via [OpenRouter](https://openrouter.ai)).
3. A IA pode **inspecionar** um pouco mais e, só se você permitir depois, **reiniciar um serviço** da lista ou aplicar patches do sistema.
4. O plantonista posta o resumo num canal do Discord.

Por padrão o sistema está em **ensaio** (`DRY_RUN=true`): ele **não muda nada** no servidor. Só observa e avisa. Deixe assim até você confiar nas mensagens.

O que ele **não** faz:

- não controla outros computadores
- não tem site nem tela gráfica
- não é um antivírus
- não substitui backup nem monitoramento profissional

---

## 2. O que você precisa ter pronto

| Item | Para quê |
| --- | --- |
| Um servidor **openSUSE** (Leap ou Tumbleweed) | É o computador que será vigiado |
| Acesso `root` (senha de administrador) | Para instalar usuário, arquivos e o agendador |
| Conta no [OpenRouter](https://openrouter.ai) + créditos | Paga a IA que lê o relatório |
| Um servidor no Discord + permissão de gerenciar o canal | Onde chegam os avisos |
| Git e Python 3.9 ou mais novo | Já vêm no openSUSE; o instalador usa os dois |

Custos: o Discord é grátis. O OpenRouter cobra pelo uso do modelo. Comece com pouco crédito e deixe o ensaio ligado.

---

## 3. Criar a chave da IA (OpenRouter)

A **chave de API** é uma senha longa que o programa usa para falar com a IA. Trate-a como senha de banco: não cole no Discord, não mande por e-mail, não suba no GitHub.

1. Abra [https://openrouter.ai](https://openrouter.ai) e crie uma conta.
2. Coloque um pouco de crédito na conta (Settings → Credits / Billing).
3. Vá em **Keys** (chaves) e crie uma chave nova.
4. Copie o texto que começa com `sk-` e guarde num bloco de notas **só no seu computador**. Você só verá a chave completa uma vez.

O modelo padrão do projeto é `anthropic/claude-sonnet-4`, com reserva `openai/gpt-4o-mini` se o primeiro falhar. Você pode trocar depois no arquivo de configuração.

---

## 4. Criar o webhook do Discord

Um **webhook** é um endereço secreto. Quem tiver o link consegue postar no canal. Não compartilhe.

1. Abra o Discord no computador.
2. Entre no servidor (ou crie um só para alertas).
3. Clique com o botão direito no canal (por exemplo `#infra`) → **Editar canal**.
4. **Integrações** → **Webhooks** → **Novo webhook**.
5. Dê um nome (`infra-harness`) e escolha o canal.
6. Clique em **Copiar URL do webhook**. Guarde junto com a chave do OpenRouter.

Opcional: se quiser que um cargo (role) seja mencionado só em alerta crítico, anote o ID do cargo. Sem isso, o programa só posta o texto, sem mencionar ninguém.

---

## 5. Instalar no servidor openSUSE

Faça estes passos **no servidor**, com uma sessão de administrador.

### Passo 5.1 — Atualizar e instalar Git

```bash
sudo zypper refresh
sudo zypper install -y git python3 python3-venv
```

Se `python3 -V` mostrar 3.9 ou maior, siga em frente.

### Passo 5.2 — Baixar o código

```bash
cd /root
git clone https://github.com/K4dmo/opens-use-infra-harness.git
cd opens-use-infra-harness
```

### Passo 5.3 — Rodar o instalador

O script cria:

- o usuário de sistema `infra-agent` (não faz login interativo)
- a pasta do programa em `/opt/infra-harness`
- dados em `/var/lib/infra-harness`
- o arquivo de senhas em `/etc/infra-harness.env`
- permissões de `sudo` **limitadas** (não é `sudo` irrestrito)
- os serviços do systemd (o “agendador” do Linux)

```bash
sudo bash deploy/install.sh
```

Se aparecer `run as root`, use `sudo` como acima.

---

## 6. Preencher a configuração

Abra o arquivo (é um texto simples, um ajuste por linha):

```bash
sudo nano /etc/infra-harness.env
```

No nano: edite, depois `Ctrl+O` para salvar e `Ctrl+X` para sair. No vim, use `i` para editar, `Esc`, depois `:wq`.

Preencha **pelo menos** estas duas linhas (cole os valores que você guardou, sem aspas e sem espaço no fim):

```bash
OPENROUTER_API_KEY=cole_aqui_a_chave_sk
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/cole_o_resto_aqui
```

Deixe estes valores **exatamente assim** na primeira semana:

```bash
DRY_RUN=true
ALLOW_PATCHES=false
USE_SUDO=true
INTERVAL_SECONDS=900
```

O que cada um significa:

| Variável | Significado para leigo |
| --- | --- |
| `DRY_RUN=true` | Ensaio: descreve o que faria, mas **não altera** o sistema |
| `ALLOW_PATCHES=false` | Não aplica atualizações `zypper patch` sozinho |
| `USE_SUDO=true` | O usuário `infra-agent` usa sudo só nos comandos permitidos |
| `INTERVAL_SECONDS=900` | 900 segundos = 15 minutos (igual ao timer) |
| `ALLOWED_UNITS` | Lista de serviços que **poderiam** ser reiniciados (sshd, cron, nginx) |
| `ALLOWED_PATHS` | Pastas em que o programa pode ler/escrever arquivos |
| `CIRCUIT_FAILURE_THRESHOLD=3` | Depois de 3 janelas com falha, ele para de tentar mudar o sistema |

Salve o arquivo. Confira que só root lê as chaves:

```bash
sudo chmod 0600 /etc/infra-harness.env
sudo ls -l /etc/infra-harness.env
```

Deve aparecer algo como `-rw-------` (só o dono lê e escreve).

---

## 7. Primeiro teste (uma vez só)

Ainda **não** ligue o agendador. Rode uma “janela” manualmente:

```bash
sudo systemctl start infra-harness.service
sudo journalctl -u infra-harness.service -n 50 --no-pager
```

O segundo comando mostra o diário do programa. Se não houver erro gritante, abra o Discord.

O que você deve ver no canal:

- nome do host
- carga, memória, disco
- serviços falhos (se houver)
- patches pendentes (`zypper`)
- gravidade: **OK**, **WARN** ou **CRIT**
- o que a IA concluiu e o que faria (em ensaio)

Se a chave da IA estiver vazia, ainda assim deve chegar um recado com o snapshot básico. Sem webhook do Discord o programa recusa iniciar.

Teste alternativo, como o usuário do serviço:

```bash
sudo -u infra-agent PYTHONPATH=/opt/infra-harness/src \
  /opt/infra-harness/.venv/bin/python -m harness --once
```

---

## 8. Ligar a verificação automática

Quando o teste único funcionar:

```bash
sudo systemctl enable --now infra-harness.timer
sudo systemctl list-timers infra-harness.timer
```

Isso faz:

- a primeira execução cerca de **2 minutos** após o boot
- as seguintes a cada **15 minutos**
- se o servidor estiver desligado na hora, o `Persistent=true` tenta recuperar a execução perdida

O modo recomendado é esse: **timer + uma execução e sai**. Se uma rodada travar, a próxima ainda pode começar.

Há um modo alternativo (processo que dorme em loop). **Não ligue os dois ao mesmo tempo.**

```bash
# só se você realmente quiser o loop contínuo em vez do timer:
sudo systemctl disable --now infra-harness.timer
sudo systemctl enable --now infra-harness-loop.service
```

Para voltar ao modo recomendado:

```bash
sudo systemctl disable --now infra-harness-loop.service
sudo systemctl enable --now infra-harness.timer
```

---

## 9. Como usar no dia a dia

Você não “abre o programa”. Você lê o Discord.

### Gravidade

- **OK** — máquina utilizável, nada urgente
- **WARN** — atenção (disco/memória altos, patches pendentes, journal barulhento)
- **CRIT** — risco (serviço importante falhou, disco quase cheio, o harness não consegue inspecionar)

### Onde olhar se algo der errado

```bash
# últimas execuções do timer
sudo systemctl status infra-harness.timer
sudo systemctl status infra-harness.service

# log
sudo journalctl -u infra-harness.service -n 80 --no-pager

# o que o programa gravou da última janela
sudo cat /var/lib/infra-harness/state.json

# histórico linha a linha (auditoria)
sudo tail -n 30 /var/lib/infra-harness/audit.jsonl
```

### Pausar e retomar

```bash
sudo systemctl stop infra-harness.timer      # para de agendar
sudo systemctl start infra-harness.timer     # volta a agendar
sudo systemctl disable --now infra-harness.timer   # para e não volta no reboot
```

### Atualizar o código depois de um `git pull`

```bash
cd /root/opens-use-infra-harness
sudo git pull
sudo bash deploy/install.sh
```

O instalador **não sobrescreve** `/etc/infra-harness.env` se o arquivo já existir. Suas chaves permanecem.

---

## 10. Quando sair do ensaio (com cuidado)

Só depois de **várias** mensagens no Discord que façam sentido para a sua máquina.

1. Confirme que `ALLOWED_UNITS` lista só serviços que você aceita reiniciar. Se você não usa nginx, tire `nginx.service`.
2. Se mudar essa lista, edite também `deploy/sudoers` (as linhas `INFRA_SYSTEMCTL_MUT`) e reinstale, senão o Linux bloqueia o sudo mesmo com a variável certa.
3. Troque no env:

```bash
DRY_RUN=false
```

4. Deixe `ALLOW_PATCHES=false` até você querer que o programa aplique `zypper patch` sozinho. Patches podem reiniciar serviços.
5. Rode **uma** janela de teste e leia o Discord e o `audit.jsonl`.
6. Só então deixe o timer ligado.

Se o programa falhar várias vezes seguidas (IA fora do ar ou comando que altera o sistema retornando erro), o **disjuntor** abre: ele para de tentar mudanças. Para resetar:

- coloque `CIRCUIT_RESET=true` no env, rode uma janela, depois volte para `false`, **ou**
- apague `/var/lib/infra-harness/state.json` (a memória da última execução some; os backups em `backups/` ficam).

---

## 11. Problemas comuns

**Nada aparece no Discord**

- Confira se colou a URL inteira do webhook.
- O canal ainda existe? O webhook foi apagado?
- Rode o teste único e leia `journalctl`.

**Mensagem chega, mas diz que falta a chave da IA**

- `OPENROUTER_API_KEY` vazio ou com espaço extra.
- Sem créditos no OpenRouter.

**`run as root` no instalador**

- Falta `sudo` na frente do `bash deploy/install.sh`.

**Comando recusado / `denied`**

- É esperado: o programa bloqueia `rm`, `dd`, firewall, `curl`, senhas, etc.
- Serviço fora de `ALLOWED_UNITS` também é recusado.

**macOS ou notebook de casa**

- Você pode testar o envio ao Discord, mas `systemctl`, `zypper` e `journalctl` vão aparecer como ausentes. Isso não substitui a instalação no openSUSE.

Teste em um computador pessoal (ensaio, sem sudo):

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp config.example.env config.env
# preencha as duas chaves; ajuste:
# DATA_DIR=./data
# USE_SUDO=false
PYTHONPATH=src .venv/bin/python -m harness --once --env-file config.env
```

---

## 12. Checklist rápido

1. Conta OpenRouter + chave + crédito  
2. Webhook do Discord copiado  
3. `git clone` no servidor openSUSE  
4. `sudo bash deploy/install.sh`  
5. Editar `/etc/infra-harness.env` (chaves; `DRY_RUN=true`)  
6. `sudo systemctl start infra-harness.service` e olhar o Discord  
7. `sudo systemctl enable --now infra-harness.timer`  
8. Só depois de alguns dias: pensar em `DRY_RUN=false`

Se um passo falhar, pare e leia o `journalctl` antes de ligar o timer. O ensaio existe para você ver o plantonista trabalhar **sem** entregar a chave da máquina.
