# Guia passo a passo — do zero ao bot rodando sozinho

> Escrito para quem **nunca** usou GitHub Actions, BotFather nem Web App do Apps Script.
> Cada clique está descrito. Onde você precisa **trocar** algo, aparece `ENTRE_SINAIS_ASSIM`
> e o guia diz se o valor vai **com ou sem aspas**.
> Tempo estimado: **2 a 3 horas** na primeira vez, com calma.

**Convenção dos comandos.** Todo comando vem com duas linhas de comentário em cima:
`# Executor:` (quem/onde roda) e `# Custo:` (quanto custa). Linhas que começam com `#`
são comentários: **não precisam ser digitadas** (se colar junto, o PowerShell ignora).

---

## Sumário

0. [Glossário mínimo](#etapa-0--glossário-mínimo)
1. [Como o bot pensa (visão geral)](#etapa-1--como-o-bot-pensa)
2. [Plano B para HOJE: alertas sem código](#etapa-2--plano-b-para-hoje-alertas-sem-código)
3. [Instalar Git e Python no Windows 11](#etapa-3--instalar-git-e-python-no-windows-11)
4. [Trazer o projeto do Google Drive para o PC](#etapa-4--trazer-o-projeto-do-google-drive-para-o-pc)
5. [Rodar e testar no seu PC](#etapa-5--rodar-e-testar-no-seu-pc)
6. [Criar o bot no Telegram](#etapa-6--criar-o-bot-no-telegram)
7. [Criar o Web App da agenda (Apps Script)](#etapa-7--criar-o-web-app-da-agenda-apps-script)
8. [Criar o repositório no GitHub e enviar o código](#etapa-8--criar-o-repositório-no-github-e-enviar-o-código)
9. [Cadastrar os Secrets](#etapa-9--cadastrar-os-secrets)
10. [Primeiras execuções no GitHub Actions](#etapa-10--primeiras-execuções-no-github-actions)
11. [Ajustar o config.yaml (campo a campo)](#etapa-11--ajustar-o-configyaml-campo-a-campo)
12. [Solução de problemas](#etapa-12--solução-de-problemas)
13. [Desafios para a v2](#etapa-13--desafios-para-a-v2)

---

## Etapa 0 — Glossário mínimo

| Termo | O que é, sem enrolação |
|---|---|
| **Repositório (repo)** | Pasta de projeto versionada pelo Git. No GitHub ela fica online. |
| **Commit** | "Foto" do projeto num momento, com uma mensagem dizendo o que mudou. |
| **Push** | Enviar seus commits do PC para o GitHub. |
| **Workflow** | Arquivo `.yml` em `.github/workflows/` que diz ao GitHub "rode estes passos quando X acontecer". |
| **Runner** | A máquina virtual Linux (Ubuntu) que o GitHub liga para rodar o workflow e desliga ao final. Toda execução começa **zerada**. |
| **Cron** | Formato de agendamento herdado do Linux: `minuto hora dia mês dia-da-semana`. `17 9 * * *` = "todo dia às 9h17". O GitHub usa **UTC**. |
| **Secret** | Valor sensível (senha, token) guardado criptografado no GitHub e entregue ao workflow como variável de ambiente. Ninguém consegue ler de volta, nem você. |
| **Variável de ambiente** | Valor que o sistema operacional entrega ao programa sem estar escrito no código. É como os Secrets chegam ao Python. |
| **API** | Porta de entrada para **programas** conversarem com um serviço (em vez de humanos clicando). |
| **JSON** | Formato de texto para dados estruturados: `{"preco": 1799.0}`. |
| **YAML** | Outro formato de dados, mais legível para humanos. **A indentação (espaços) tem significado** — nunca use TAB. |
| **JSON-LD** | Bloco de dados escondido no HTML das páginas de produto, no padrão schema.org, que as lojas publicam para o Google. |
| **robots.txt** | Arquivo na raiz de um site (`site.com/robots.txt`) dizendo o que robôs podem ou não acessar. |
| **WAF** | *Web Application Firewall*: o "porteiro" do site que barra tráfego suspeito. |
| **Webhook** | Uma URL que, ao receber um pedido, executa uma ação (aqui: criar evento na agenda). |
| **venv** | "Ambiente virtual" do Python: uma pasta com bibliotecas só deste projeto, sem bagunçar o resto do PC. |
| **Lint** | Verificação automática de estilo e erros comuns no código (usamos o `ruff`). |
| **CI** | *Continuous Integration*: rodar testes automaticamente a cada mudança no código. |

---

## Etapa 1 — Como o bot pensa

A cada execução (3 vezes por dia), em ordem:

1. **Lê e valida o `config.yaml`** — marcas, BTUs, teto, modelos e a lista de **páginas vigiadas**.
   Se houver erro de digitação (ex.: um `modelo` que não existe), o bot para ANTES de acessar a internet.
2. **Visita cada página vigiada** — antes de tudo, lê o `robots.txt` do site e obedece. Entre uma
   requisição e outra ao mesmo site, espera 10 segundos. Recusa (HTTP 4xx/5xx) = o bot desiste daquele
   site na rodada; HTTP 404 = só aquela URL está desatualizada.
3. **Lê o JSON-LD** de cada página: nome, preço e se está em estoque.
4. **Filtra** cada anúncio (`bot/filtros.py`):
   - é de uma das 3 marcas? é inverter? é 9k ou 12k? **não** é quente/frio? não diz 110/127V? não é peça/kit/usado?
   - o "RG" do modelo vem do campo `modelo` que você pôs na URL ou, na falta dele, do **código de fábrica**
     encontrado na página (ex.: `midea-ai-ecomaster-9k-frio-vca`).
5. **Aplica as regras de compra** (`bot/regras.py`): teto de R$ 2.500; só o que está em estoque; 12k só
   aparece se for mais barato que o melhor 9k da rodada.
6. **Procura alertas:**
   - 🟢 **Voltou ao estoque**: a página estava esgotada na rodada anterior e voltou, dentro do teto → Telegram.
   - 📉 **Queda**: a mesma URL ficou ≥ 8% mais barata desde a última execução → Telegram.
   - 🔥 **Diferença grande**: o mesmo modelo está ≥ 15% **e** ≥ R$ 250 mais barato numa loja do que em outra → Telegram **e** evento vermelho no Google Agenda.
7. **Envia o resumo** no Telegram (até 15 ofertas, da mais barata para a mais cara), quantas páginas estão
   esgotadas e as falhas da rodada.
8. **Salva o histórico** (preço e estoque de cada página) em `data/historico.json`; o workflow faz commit
   desse arquivo, para a próxima execução lembrar.

> **Limite importante:** o bot **vigia** as páginas da lista; ele **não descobre** anúncios novos.
> Descoberta = você + os alertas da Etapa 2. Achou um anúncio bom? Cole a URL em `urls_vigiadas`.
> O preço lido é o da página (em geral o do cartão); o Pix costuma ser mais barato.

Alertas iguais não se repetem: o bot anota cada alerta enviado e não manda o mesmo de novo (diferença: no máximo 1 por modelo/loja/preço por dia).

---

## Etapa 2 — Plano B para HOJE: alertas sem código

A Black Friday é **27/11**. Enquanto você monta o bot, ligue estes alertas (10 minutos, custo zero):

1. **Google Shopping:** pesquise no Google `Gree G-Top Auto 9000 frio GWC09ATB-D6DNA1A` → aba **Shopping** →
   abra o produto → ative **Acompanhar preço** (precisa estar logado na conta Google).
   Repita para `Midea AI Ecomaster 9000 frio 42EZVCA09M5` e `Elgin Eco II 9000 frio HJFI09C2IA`.
2. **Promobit** (app ou site) → **Lista de desejos** → cadastre palavras-chave com no máximo 4 palavras:
   `gree inverter 9000 frio`, `midea inverter 9000 frio`, `elgin inverter 9000 frio`.
3. **Pelando** (app) → **Alertas** → mesmas palavras-chave.

Esses alertas cobrem justamente as lojas que o bot **não** consegue ler (Amazon, Magalu, Casas Bahia, Mercado Livre).

---

## Etapa 3 — Instalar Git e Python no Windows 11

### 3.1 Abrir o PowerShell
1. Clique no botão **Iniciar** (logo do Windows).
2. Digite `PowerShell`.
3. Clique em **Windows PowerShell** (não precisa ser "como administrador").

### 3.2 Instalar o Git e o Python com o winget
O `winget` é o gerenciador de pacotes oficial do Windows (equivalente ao `dnf` do seu Fedora).

```powershell
# Executor: você, no PowerShell do seu PC (Windows 11)
# Custo: US$ 0,00 (software gratuito, instalado localmente)
winget install --id Git.Git -e --source winget
winget install --id Python.Python.3.12 -e --source winget
```
- `--id`: o identificador exato do pacote no repositório do winget.
- `-e` (*exact*): só aceita o ID exatamente igual (evita instalar pacote parecido).
- `--source winget`: usa o repositório oficial, não a Microsoft Store.

Se aparecer pergunta sobre aceitar termos, digite `Y` e Enter.

### 3.3 Fechar e reabrir o PowerShell
Feche a janela e abra de novo (Etapa 3.1). **Motivo:** o Windows só atualiza a variável `PATH`
(a lista de pastas onde ele procura programas) em janelas novas.

### 3.4 Conferir as instalações
```powershell
# Executor: você, no PowerShell do seu PC
# Custo: US$ 0,00 (local)
git --version
py -3.12 --version
```
Esperado: algo como `git version 2.x` e `Python 3.12.x`.
Usamos `py -3.12` (o "lançador" do Python no Windows) em vez de `python`, porque no Windows 11 o
comando `python` às vezes abre a Microsoft Store.

### 3.5 Dizer ao Git quem é você
O Git grava autor em cada commit. **Dica de segurança:** não use seu e-mail pessoal num repositório público —
use o e-mail anônimo do GitHub. Você descobre ele na Etapa 8.1 (passo 6). Se ainda não tem conta no GitHub,
faça a Etapa 8.1 primeiro e volte aqui.

```powershell
# Executor: você, no PowerShell do seu PC
# Custo: US$ 0,00 (local)
git config --global user.name "SEU_NOME"
git config --global user.email "SEU_EMAIL_NOREPLY_DO_GITHUB"
git config --global init.defaultBranch main
```
- Troque `SEU_NOME` pelo seu nome — **mantenha as aspas duplas** (o nome tem espaço).
- Troque `SEU_EMAIL_NOREPLY_DO_GITHUB` pelo e-mail do tipo `12345678+usuario@users.noreply.github.com` — **mantenha as aspas**.
- `--global`: vale para todos os repositórios deste usuário do Windows.
- `init.defaultBranch main`: novos repositórios começam no ramo `main` (padrão atual do GitHub).

---

## Etapa 4 — Trazer o projeto do Google Drive para o PC

1. Abra **drive.google.com** no navegador.
2. Na raiz de **Meu Drive**, ache a pasta **`monitor-ar-condicionado`**.
3. Clique com o **botão direito** na pasta → **Fazer download**. O Drive gera um `.zip` e baixa.
4. Crie a pasta `C:\projetos` (Explorador de Arquivos → Disco Local (C:) → botão direito → Novo → Pasta → `projetos`).
5. Vá em **Downloads**, clique com o botão direito no `.zip` → **Extrair tudo...** → em "Os arquivos serão extraídos para esta pasta", escreva `C:\projetos` → **Extrair**.
6. Confira que existe `C:\projetos\monitor-ar-condicionado\config.yaml`.
   Se o Drive criou uma pasta a mais no meio (ex.: `C:\projetos\monitor-ar-condicionado-2026...\monitor-ar-condicionado`),
   mova a pasta `monitor-ar-condicionado` de dentro para `C:\projetos\` e apague a sobra.
7. Confira que existe a pasta **`.github`** (com ponto) contendo `workflows\monitor.yml` e `workflows\ci.yml`.
   **Sem ela, o GitHub não roda nada.**

> O Drive é a **vitrine/backup** do portfólio. A cópia que **vale** passa a ser a do GitHub:
> toda alteração futura deve ser feita no repositório e, se quiser, copiada para o Drive depois.

---

## Etapa 5 — Rodar e testar no seu PC

### 5.1 Entrar na pasta do projeto
```powershell
# Executor: você, no PowerShell do seu PC
# Custo: US$ 0,00 (local)
cd C:\projetos\monitor-ar-condicionado
```

### 5.2 Permitir scripts de ambiente virtual (só uma vez na vida)
O PowerShell bloqueia scripts por padrão. O comando abaixo libera **só** scripts locais e assinados
remotamente, **só** para o seu usuário — é o ajuste recomendado pela Microsoft para desenvolvedores.
```powershell
# Executor: você, no PowerShell do seu PC
# Custo: US$ 0,00 (local)
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```
Se perguntar, responda `S` (ou `Y`) e Enter.

### 5.3 Criar e ativar o ambiente virtual
```powershell
# Executor: você, no PowerShell do seu PC
# Custo: US$ 0,00 (local)
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```
- `-m venv .venv`: cria a pasta `.venv` com um Python isolado.
- `Activate.ps1`: "liga" o ambiente. O prompt passa a começar com `(.venv)`.
  **Toda vez** que abrir um PowerShell novo para este projeto, repita a linha do `Activate.ps1`.

### 5.4 Instalar as bibliotecas
```powershell
# Executor: você, no PowerShell do seu PC, com (.venv) ativo
# Custo: US$ 0,00 (download de pacotes gratuitos do PyPI)
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```
`requirements-dev.txt` instala `requests` (HTTP), `PyYAML` (ler o config), `pytest` (testes) e `ruff` (lint).

### 5.5 Rodar os testes automáticos
```powershell
# Executor: você, no PowerShell do seu PC, com (.venv) ativo
# Custo: US$ 0,00 (local, sem internet)
ruff check bot tests
python -m pytest -v
```
Esperado: `All checks passed!` e `22 passed`. Os testes usam os arquivos de `tests/fixtures/` (respostas de
exemplo), então **não acessam nenhuma loja**.

### 5.6 Diagnóstico das páginas vigiadas (primeiro contato real com a internet)
```powershell
# Executor: você, no PowerShell do seu PC, com (.venv) ativo
# Custo: US$ 0,00 (≈ 20 requisições HTTP às lojas, a partir da sua internet de casa)
python -m bot.main --diagnostico
```
Para cada página aparece um bloco como:
```
Midea (loja oficial) | modelo no config: midea-ai-ecomaster-9k-frio-vca
https://www.midea.com.br/...
  HTTP 200 | Content-Type: text/html; charset=utf-8
  Título: Ar Condicionado 9000 BTUs Inverter | Midea
  Preço: R$ 2188.42 | Em estoque: NÃO
  Filtros: APROVADA | modelo detectado: midea-ai-ecomaster-9k-frio-vca
```

| Resultado | Significa | O que fazer |
|---|---|---|
| `HTTP 200` + `Filtros: APROVADA` | Página lida e aparelho certo | Manter |
| `Filtros: REPROVADA (motivo)` | A página é lida, mas o aparelho não serve (ex.: quente/frio) | Remover a URL |
| `⚠️ CONFIRA o config` | A página traz o código de outro modelo | Corrigir o campo `modelo` da URL |
| `PaginaNaoEncontrada: HTTP 404` | URL desatualizada (produto saiu do ar) | Trocar a URL |
| `SiteBloqueado: HTTP 4xx` ou `robots.txt: PROIBIDO` | O site recusa robôs | Remover a loja. **Não** tentamos contornar (ADR-003) |
| `Sem JSON-LD de produto` | A página não publica os dados estruturados | Remover a URL |

Anote o resultado: na Etapa 10 você roda o mesmo diagnóstico **no GitHub**, e a comparação ensina muito
(IP de casa versus IP de datacenter).

### 5.7 Rodar em modo simulação
```powershell
# Executor: você, no PowerShell do seu PC, com (.venv) ativo
# Custo: US$ 0,00 (≈ 20 requisições HTTP às lojas)
python -m bot.main --simular
```
- `--simular`: faz a rodada inteira, mas **não envia** nada e **não salva** histórico.
- O log mostra também os anúncios **descartados** e o motivo — ótimo para entender os filtros.

Ao final aparece a mensagem exatamente como ela chegaria no Telegram (com as tags `<b>` visíveis; no Telegram elas viram negrito).

---

## Etapa 6 — Criar o bot no Telegram

### 6.1 Falar com o BotFather
1. No Telegram, toque na **lupa** e pesquise `BotFather`.
2. Abra o que tem o **selo azul de verificado** e o usuário exatamente `@BotFather` (existem imitações).
3. Toque em **Iniciar** (ou envie `/start`).
4. Envie `/newbot`.
5. Ele pede um **nome de exibição**: envie, por exemplo, `Monitor Ar Juliano`.
6. Ele pede um **username**, que precisa terminar em `bot`: envie, por exemplo, `monitor_ar_juliano_bot`
   (se estiver ocupado, tente outra variação).
7. Ele responde com uma mensagem contendo o **token**, no formato `1234567890:AAH...` (números, dois-pontos, letras).
   **Copie o token inteiro.** Ele é a "senha" do bot: quem tem o token controla o bot.

### 6.2 Descobrir o seu chat ID
O bot só pode mandar mensagem para quem falou com ele primeiro.
1. Na mensagem do BotFather, toque no link `t.me/monitor_ar_juliano_bot` (o seu).
2. Toque em **Iniciar** e envie qualquer mensagem, ex.: `oi`.
3. No navegador do **PC**, abra o endereço abaixo, trocando `SEU_TOKEN` pelo token (**sem aspas**, sem espaços,
   e mantendo a palavra `bot` colada antes do token):
   ```
   https://api.telegram.org/botSEU_TOKEN/getUpdates
   ```
4. Vai aparecer um texto JSON. Procure o trecho `"chat":{"id":` — o número logo depois (ex.: `987654321`) é o **chat ID**.
   - Se aparecer só `{"ok":true,"result":[]}`, envie outra mensagem ao bot e recarregue a página (F5).
5. **Higiene de segurança:** esse endereço com o token ficou no histórico do navegador. Apague-o
   (Ctrl+H → passe o mouse na entrada → X).

### 6.3 Testar do PC
```powershell
# Executor: você, no PowerShell do seu PC, com (.venv) ativo
# Custo: US$ 0,00 (API de bots do Telegram é gratuita)
$env:TELEGRAM_TOKEN = "SEU_TOKEN"
$env:TELEGRAM_CHAT_ID = "SEU_CHAT_ID"
python -m bot.main --testar telegram
```
- Troque `SEU_TOKEN` e `SEU_CHAT_ID`, **mantendo as aspas duplas**.
- Esperado no celular: `✅ Teste: o bot consegue falar com você no Telegram.`

---

## Etapa 7 — Criar o Web App da agenda (Apps Script)

### 7.1 Gerar o token secreto do webhook
```powershell
# Executor: você, no PowerShell do seu PC, com (.venv) ativo
# Custo: US$ 0,00 (local)
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
Copie o resultado (uma sequência de ~43 caracteres). Ele será usado em **dois lugares**: no Apps Script
e no GitHub. `secrets` é o módulo do Python feito para gerar valores aleatórios **criptograficamente seguros**.

### 7.2 Criar o projeto
1. Abra **script.google.com**, logado na conta Google **cuja agenda aparece no seu celular**.
2. Clique em **Novo projeto** (canto superior esquerdo).
3. No topo, clique em **Projeto sem título** → digite `monitor-ar-agenda` → **Renomear**.
4. No editor, o arquivo `Código.gs` já vem com `function myFunction() {}`. Selecione **tudo** (Ctrl+A) e apague.
5. Abra o arquivo `apps_script\Codigo.gs` do projeto (no Bloco de Notas ou VS Code), copie **tudo** e cole no editor.
6. Salve: **Ctrl+S** (ou ícone de disquete).

### 7.3 Guardar o token nas Propriedades do script
1. Na barra lateral esquerda, clique na **engrenagem** (**Configurações do projeto**).
2. Role até o fim: **Propriedades do script** → **Adicionar propriedade do script**.
3. Em **Propriedade**, escreva `TOKEN` (maiúsculas, **sem aspas**).
4. Em **Valor**, cole o token da Etapa 7.1 (**sem aspas**).
5. Clique em **Salvar propriedades do script**.

> Por que não escrever o token direto no código? Porque código a gente copia, compartilha, publica no
> GitHub. Propriedades do script ficam fora do código — mesma ideia dos GitHub Secrets.

### 7.4 Testar e autorizar o acesso à agenda
1. Volte ao editor (ícone **`< >`** na barra lateral).
2. Na barra de cima, no seletor de função (ao lado de **Depurar**), escolha **`testarManualmente`**.
3. Clique em **Executar**.
4. Aparece **"Autorização necessária"** → **Revisar permissões** → escolha sua conta.
5. Aparece **"O Google não verificou este app"**. É esperado: o app é **seu** e não foi publicado.
   Clique em **Avançado** → **Acessar monitor-ar-agenda (não seguro)** → **Permitir**.
   - Você está autorizando o **seu próprio** script a criar eventos na **sua** agenda.
6. No **Registro de execução** (embaixo) deve aparecer `{"ok":true,"id":"..."}`.
7. Abra o Google Agenda: há um evento vermelho **"TESTE do monitor de ar-condicionado"** daqui a 10 minutos,
   e o celular deve avisar em ~1 minuto. Pode apagar o evento depois.

### 7.5 Publicar como Web App
1. Clique em **Implantar** (botão azul, canto superior direito) → **Nova implantação**.
2. Ao lado de "Selecione o tipo", clique na **engrenagem** → **App da Web**.
3. **Descrição:** `v1`.
4. **Executar como:** **Eu (seu e-mail)** ← essencial: o evento é criado como você e o lembrete toca no seu celular.
5. **Quem pode acessar:** **Qualquer pessoa**. (O GitHub não tem login Google; a proteção é o token.)
6. Clique em **Implantar** (pode pedir autorização de novo — repita a Etapa 7.4, passo 5).
7. Copie a **URL do app da Web**: começa com `https://script.google.com/macros/s/` e **termina em `/exec`**.
   (Uma URL terminada em `/dev` só funciona logada; não use.)

> ⚠️ **Se um dia você editar o `Codigo.gs`:** salvar **não** atualiza o Web App.
> Vá em **Implantar → Gerenciar implantações → lápis (Editar) → Versão: Nova versão → Implantar**.
> A URL continua a mesma.

### 7.6 Testar do PC
```powershell
# Executor: você, no PowerShell do seu PC, com (.venv) ativo
# Custo: US$ 0,00 (Apps Script é gratuito dentro das cotas da conta Google)
$env:AGENDA_WEBHOOK_URL = "URL_DO_WEB_APP_TERMINADA_EM_EXEC"
$env:AGENDA_WEBHOOK_TOKEN = "TOKEN_DA_ETAPA_7_1"
python -m bot.main --testar agenda
```
- Troque os dois valores, **mantendo as aspas duplas**.
- Esperado: `Evento de teste na agenda: criado` e um evento vermelho "🔥 AR-CONDICIONADO 33% mais barato na Loja Teste".

---

## Etapa 8 — Criar o repositório no GitHub e enviar o código

### 8.1 Conta no GitHub (pule se já tiver)
1. Acesse **github.com** → **Sign up** → siga o cadastro (e-mail, senha, username).
   - O **username** aparece no portfólio (`github.com/username`). Escolha algo profissional.
2. Ative a **autenticação em dois fatores**: foto de perfil (canto superior direito) → **Settings** →
   **Password and authentication** → **Enable two-factor authentication** (use um app autenticador).
3. Ainda em **Settings**, menu lateral **Emails** → marque **Keep my email addresses private**.
4. Logo abaixo aparece o seu e-mail anônimo, do tipo `12345678+username@users.noreply.github.com`.
   Use **esse** na Etapa 3.5.

### 8.2 Criar o repositório vazio
1. No GitHub, clique no **+** (canto superior direito) → **New repository**.
2. **Repository name:** `monitor-ar-condicionado`.
3. **Description:** `Bot de monitoramento de preços com GitHub Actions, Telegram e Google Agenda`.
4. Marque **Public** (Actions gratuito + vitrine de portfólio).
5. **NÃO** marque "Add a README file", **nem** .gitignore, **nem** license — o projeto já tem os arquivos, e marcar isso cria um conflito no primeiro envio.
6. Clique em **Create repository**.

### 8.3 Enviar o código (primeiro push)
```powershell
# Executor: você, no PowerShell do seu PC, dentro de C:\projetos\monitor-ar-condicionado
# Custo: US$ 0,00 (GitHub gratuito para repositório público)
git init
git add .
git status
```
- `git init`: transforma a pasta em repositório Git (cria a pasta oculta `.git`).
- `git add .`: coloca **todos** os arquivos na "área de preparação" (*staging*) do próximo commit.
- `git status`: lista o que vai entrar. **Confira que `.venv` NÃO aparece** — o `.gitignore` deve escondê-la.
  Se aparecer, pare e me chame.

```powershell
# Executor: você, no PowerShell do seu PC
# Custo: US$ 0,00
git commit -m "feat: primeira versão do monitor de preços"
git remote add origin https://github.com/SEU_USUARIO/monitor-ar-condicionado.git
git push -u origin main
```
- Troque `SEU_USUARIO` pelo seu username do GitHub (**sem aspas**, a URL inteira não leva aspas).
- `commit -m`: cria a "foto" com a mensagem. O prefixo `feat:` segue a convenção *Conventional Commits*, muito usada em empresas.
- `remote add origin`: diz ao Git que o repositório remoto chamado `origin` fica nessa URL.
- `push -u origin main`: envia o ramo `main` e lembra essa ligação (`-u`) para os próximos `git push`.
- Na primeira vez, abre uma janela do **Git Credential Manager** pedindo login no GitHub pelo navegador → **Sign in with your browser** → autorize. Sem senha no terminal.

Recarregue a página do repositório: os arquivos devem aparecer. Clique na aba **Actions**: o workflow **CI** já deve estar rodando por causa do push. Em ~1 minuto, ✅ verde.

---

## Etapa 9 — Cadastrar os Secrets

1. No repositório, clique na aba **Settings** (engrenagem, à direita das abas).
2. Menu lateral: **Secrets and variables** → **Actions**.
3. Clique em **New repository secret**. Para **cada linha** da tabela abaixo:
   - **Name:** o nome exatamente como na tabela (maiúsculas, sem espaços, **sem aspas**);
   - **Secret:** o valor (**sem aspas**);
   - **Add secret**.

| Name | Secret (valor) | Obrigatório? |
|---|---|---|
| `TELEGRAM_TOKEN` | token do BotFather (Etapa 6.1) | Sim |
| `TELEGRAM_CHAT_ID` | número do chat (Etapa 6.2) | Sim |
| `AGENDA_WEBHOOK_URL` | URL `/exec` (Etapa 7.5) | Para a agenda |
| `AGENDA_WEBHOOK_TOKEN` | token da Etapa 7.1 | Para a agenda |

> O nome errado é a causa nº 1 de falha. `TELEGRAM_TOKEN` ≠ `TELEGRAM-TOKEN` ≠ `telegram_token`.

4. Ainda em **Settings**, menu lateral **Actions → General** → role até **Workflow permissions** →
   marque **Read and write permissions** → **Save**. Isso permite que o workflow faça commit do histórico.

---

## Etapa 10 — Primeiras execuções no GitHub Actions

Para cada modo abaixo, **na ordem**:
1. Aba **Actions** → na lista da esquerda, clique em **Monitor de preços**.
2. À direita, botão **Run workflow** → em "Use workflow from" deixe `main` → em **"O que fazer nesta execução manual"** escolha o modo → botão verde **Run workflow**.
3. Recarregue a página após uns segundos; clique na execução que apareceu → no job **monitorar** → expanda o passo **Rodar o bot** para ver o log.

| Ordem | Modo | O que valida | Esperado |
|---|---|---|---|
| 1 | `diagnostico` | Quais lojas aceitam o bot **a partir do IP do GitHub** | Compare com a Etapa 5.6 |
| 2 | `testar_telegram` | Secrets do Telegram | Mensagem ✅ no celular |
| 3 | `testar_agenda` | Secrets da agenda | Evento vermelho de teste |
| 4 | `simular` | Fluxo completo sem enviar | Mensagem impressa no log |
| 5 | `normal` | Produção | Resumo no Telegram + commit "chore(dados)..." no repositório |

Depois disso, o bot roda **sozinho** às **06:17, 12:17 e 18:17** (horário de Fortaleza), podendo atrasar alguns minutos.

**Mudar a frequência perto da Black Friday:** no `monitor.yml`, troque `"17 9,15,21 * * *"` por, por exemplo,
`"17 */3 * * *"` (a cada 3 horas). Evite menos de 2 horas: mais tráfego não melhora muito o resultado e aumenta a chance de bloqueio.

---

## Etapa 11 — Ajustar o config.yaml (campo a campo)

**Regra de ouro do YAML:** indentação com **2 espaços**, nunca TAB; texto com espaço ou acento vai entre **aspas duplas**;
listas usam `[a, b]` ou linhas começando com `- `.

**Fluxo de alteração (vale para qualquer arquivo):**
```powershell
# Executor: você, no PowerShell do seu PC, dentro da pasta do projeto, com (.venv) ativo
# Custo: US$ 0,00
git pull
python -m pytest -q
git add config.yaml
git commit -m "chore(config): descreva aqui o que mudou"
git push
```
- `git pull` **antes** de editar: o robô faz commits do histórico 3x/dia; sem o `pull` seu `push` é recusado.
- `pytest` antes do commit: se você quebrou o YAML, descobre na hora.

| Campo | O que é | Quem preenche | Exemplo |
|---|---|---|---|
| `criterios.marcas` | Marca → palavras que a identificam no título | Manual | `midea: ["midea", "springer midea"]` |
| `criterios.capacidades_btu` | BTUs aceitos | Manual | `[9000, 12000]` |
| `criterios.exigir_inverter` / `exigir_so_frio` | Liga/desliga os filtros | Manual | `true` / `false` (sem aspas) |
| `criterios.voltagem` | Voltagem exigida | Manual | `220` |
| `criterios.preco_maximo` | Teto do aparelho, sem instalação, em reais | Manual | `2500.00` (ponto decimal, sem "R$") |
| `criterios.regra_12k` | `"somente_se_mais_barato_que_9k"` ou `"sempre"` | Manual | com aspas |
| `criterios.exigir_frete_gratis` | Descarta frete pago **confirmado** (hoje nenhuma loja permite verificar; todas vêm com aviso) | Manual | `true` |
| `criterios.palavras_proibidas` | Títulos com estas palavras são descartados (peças, usados) | Manual | sem acento, minúsculas |
| `criterios.codigos_quente_frio` | Pedaços de código de fábrica que indicam quente/frio | Manual | `"gwh"` |
| `alertas.*` | Limites dos alertas (%, R$) e liga/desliga agenda | Manual | `15`, `250`, `true` |
| `educacao_http.*` | Ritmo do bot (intervalo, timeout, teto) | Manual — **não reduza o intervalo** | `10` |
| `modelos` | Modelos com código de fábrica (base da comparação entre lojas) | Manual | ver abaixo |
| `urls_vigiadas` | Páginas de produto vigiadas (o coração do bot) | Manual | ver abaixo |
| `lojas_com_instalacao_inclusa` | Nomes de lojas (iguais ao campo `nome`/`loja`) que instalam de graça | Manual | `["Loja X"]` |
| `data/historico.json` | Preços passados e alertas já enviados | **Automático — nunca edite à mão** | — |

**Adicionar um modelo:** na página do produto, procure "Especificações" → "Modelo"/"Código". Use os códigos da
evaporadora e da condensadora, em **minúsculas**, **sem** o prefixo `45`:
```yaml
  - id: gree-gtop-auto-12k-frio            # identificador único, sem espaços
    nome: "Gree G-Top Auto 12.000 BTUs Frio"
    btu: 12000                              # número, sem aspas
    codigos: ["CODIGO_EM_MINUSCULAS"]
```

**Vigiar uma página nova** (qualquer loja): acrescente um item no fim de `urls_vigiadas`,
com **2 espaços** antes do `-`:
```yaml
  - loja: "Nome da Loja"
    url: "https://www.loja.com.br/caminho-do-produto/p"
    modelo: midea-ai-ecomaster-9k-frio-vca    # opcional, sem aspas; só se você CONFERIU o código
```
- `loja` e `url` vão **entre aspas duplas**; `modelo` vai **sem aspas** e precisa ser igual a um `id` da lista `modelos`.
- Depois de salvar, rode `python -m pytest -q` (o teste `test_config_do_repositorio_e_valida` acusa erro de digitação)
  e `python -m bot.main --diagnostico` para ver se a loja aceita o bot.

---

## Etapa 12 — Solução de problemas

| Sintoma | Causa provável | Correção |
|---|---|---|
| CI com ❌ vermelho | Teste ou lint falhou | Abra a execução → passo vermelho → leia a última mensagem de erro |
| Log: `Faltam os segredos TELEGRAM_TOKEN...` | Secret ausente ou nome diferente | Etapa 9, confira letra por letra |
| Log: `Telegram recusou: HTTP 401` | Token errado | Refaça o secret `TELEGRAM_TOKEN` |
| Log: `Telegram recusou: HTTP 400 ... chat not found` | Chat ID errado ou você não apertou **Iniciar** no bot | Etapa 6.2 |
| `Evento de teste na agenda: FALHOU` | URL `/dev`, token diferente do Apps Script, ou código editado sem nova versão | Etapas 7.3 e 7.5 |
| Passo "Salvar o histórico" falha com 403 | Workflow sem permissão de escrita | Etapa 9, passo 4 |
| `git push` recusado (*rejected, fetch first*) | O robô commitou o histórico depois do seu último `pull` | `git pull` e depois `git push` |
| Diagnóstico OK no PC, `HTTP 403` no GitHub | A loja bloqueia IPs de datacenter | Decisão da loja: remova-a. Alternativa legítima: rodar só essa parte no seu PC pelo Agendador de Tarefas do Windows |
| Nenhuma oferta, nenhuma falha | Tudo esgotado ou acima do teto | Normal: o resumo mostra quantas páginas estão esgotadas; você será avisado quando voltarem |
| `ConfigInvalida: ...` | Erro de digitação no `config.yaml` | A mensagem diz o item e o campo; corrija e rode `pytest` |
| Execução agendada não aconteceu | Atraso do GitHub, ou 60 dias sem atividade | Aba Actions → veja se há aviso de workflow desativado → **Enable workflow** |

---

## Etapa 13 — Desafios para a v2

Ordenados do mais fácil para o mais difícil:
1. **Fixar as Actions por SHA** (`actions/checkout@<hash>`) e deixar o Dependabot atualizar.
2. **Gráfico de preços**: gerar um PNG do `historico.json` e mandar pelo Telegram (`sendPhoto`).
3. **Mercado Livre via OAuth**: app no DevCenter, troca de *refresh token* a cada execução e atualização automática do secret via API do GitHub.
4. **Containerizar** o bot (Dockerfile) e rodar o mesmo container no Actions — prática direta para a sua trilha DevOps.
5. **Observabilidade**: métricas por execução (lojas OK/recusadas, nº de ofertas, duração) num painel.
