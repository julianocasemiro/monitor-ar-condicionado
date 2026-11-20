# Registro de Decisões de Arquitetura (ADR)

> **O que é um ADR?** *Architecture Decision Record* é um registro curto de **uma** decisão
> técnica: o contexto, o que foi decidido, as alternativas descartadas e as consequências.
> Em empresa, ele responde à pergunta "por que diabos fizeram assim?" meses depois.
> Em portfólio, mostra que você **pensa** antes de codar — é o que recrutador de DevOps procura.

---

## ADR-001 — GitHub Actions como agendador

**Contexto.** O bot precisa rodar sozinho várias vezes por dia, mesmo com o PC desligado.

**Decisão.** Usar GitHub Actions com gatilho `schedule` (cron), em repositório **público**.

**Alternativas descartadas.**
| Alternativa | Por que não |
|---|---|
| Agendador de Tarefas no PC (Windows 11) | Depende do PC ligado; não ensina CI/CD. |
| Conta AWS da empresa | Projeto pessoal em infraestrutura corporativa é uso indevido de recurso — seria um achado de auditoria (ISO 27001, controle 5.10 — uso aceitável de ativos). |
| Tarefa agendada do Claude | Consome cota do plano a cada execução; menos aprendizado de infraestrutura. |
| VPS própria | Custo mensal e manutenção de servidor para um projeto de semanas. |

**Consequências.**
- Custo zero: o GitHub informa que o uso de Actions é gratuito para runners padrão em repositórios públicos.
- O cron do GitHub **não é pontual**: em horários de pico a execução pode atrasar. Usamos o minuto 17 para fugir da virada da hora.
- Em repositório público, workflows agendados são desativados após 60 dias sem atividade no repositório. Para uma compra de Black Friday isso é irrelevante; para uso longo, basta qualquer commit.

---

## ADR-002 — Fontes de dados: só dados estruturados e públicos

**Contexto.** O pedido original incluía "Google Shopping" e o Mercado Livre.

**Decisão.** Duas fontes, ambas publicadas **de propósito** pelas lojas:
1. **Busca pública de catálogo VTEX** (`/api/catalog_system/pub/products/search`) — endpoint documentado pela VTEX, o mesmo que a vitrine da loja usa. Também usamos a **simulação de carrinho** (`/api/checkout/pub/orderForms/simulation`) para saber o frete até o CEP.
2. **JSON-LD (schema.org/Product)** nas páginas de produto — o bloco de dados que a loja publica para o Google entender preço e disponibilidade.

**Fontes descartadas e por quê.**
| Fonte | Motivo | Substituto |
|---|---|---|
| Google Shopping | Não há API para consumidor; raspar o Google viola os termos e cai em CAPTCHA. | Alerta nativo "Acompanhar preço" do próprio Google Shopping. |
| Mercado Livre | Desde 2025 a busca exige token OAuth; o *refresh token* é de uso único e muda a cada renovação — guardar isso em GitHub Secrets exige reescrever o segredo a cada execução. Bom desafio para a **v2**. | Alertas do Promobit/Pelando. |
| Amazon, Magalu, Casas Bahia | Proteção anti-robô agressiva; IP de datacenter (GitHub) costuma ser recusado. | `urls_vigiadas` (o bot tenta com educação; se recusar, desiste) + alertas nativos. |

**Consequência.** Cobertura menor, mas estável: dados estruturados quebram muito menos que "raspar" o layout visual da página.

---

## ADR-003 — Bot educado, não furtivo

**Contexto.** Pedido: "o bot deve tomar cuidado para não levantar suspeitas do firewall".

**Decisão.** O bot **não tenta se esconder**. Ele evita ser incômodo:
- User-Agent honesto, com o link do repositório;
- obedece o `robots.txt` (RFC 9309) e o `Crawl-delay`;
- espera ≥ 10 s entre requisições ao mesmo site e tem teto de 12 requisições por site por execução;
- ao receber 401/403/429/503 ou página de verificação, **para** de acessar aquele site na execução e avisa no Telegram.

**O que foi deliberadamente NÃO feito:** rodízio de IP/proxy, falsificar navegador (User-Agent de Chrome, *fingerprint*), resolver CAPTCHA, atrasos aleatórios "para parecer humano".

**Por quê.**
1. **Ética e lei:** contornar uma barreira técnica que o dono do site colocou é ignorar um "não" explícito; pode violar os Termos de Uso da loja.
2. **Coerência profissional:** você é analista de segurança. Se visse no seu WAF um robô trocando de IP e fingindo ser Chrome, classificaria como tráfego malicioso. O portfólio deve mostrar o lado certo dessa linha.
3. **Na prática funciona melhor:** 3 execuções/dia × ~20 requisições espaçadas é tráfego desprezível. WAFs bloqueiam volume e padrão agressivo; um bot lento e identificado raramente incomoda.

---

## ADR-004 — Google Agenda via Web App do Apps Script

**Contexto.** Diferença grande de preço deve virar evento na agenda, "para ficar mais visível no celular".

**Decisão.** Um Web App do Google Apps Script (`apps_script/Codigo.gs`), publicado como **"Executar como: Eu"**, recebe um POST do bot e cria o evento com lembrete pop-up. O pedido só é aceito se trouxer um token secreto.

**Alternativa descartada.** Conta de serviço (*service account*) do Google Cloud + Calendar API. A documentação do Google diz que lembretes são informação privada de cada usuário autenticado e não são compartilhados entre usuários. Logo, um evento criado pela conta de serviço teria os lembretes **dela**, e o seu celular poderia não tocar. Além disso, exigiria projeto no Google Cloud, chave JSON e compartilhamento do calendário — mais superfície de ataque para o mesmo resultado.

**Consequência.** A URL do Web App é um segredo de fato (quem tem URL + token cria eventos na sua agenda). Ambos ficam só nos GitHub Secrets.

---

## ADR-005 — Histórico em JSON versionado no próprio repositório

**Decisão.** `data/historico.json`, com commit automático ao fim de cada execução.

**Prós:** zero infraestrutura; cada mudança de preço fica auditável no histórico do Git.
**Contras:** gera commits frequentes (ruído); não escala para milhares de produtos. Para a v2, SQLite como *artifact* ou um banco gratuito.

---

## ADR-006 — Segredos e repositório público

- Token do Telegram, chat ID, CEP e URL/token da agenda ficam em **GitHub Secrets** (criptografados; o GitHub mascara os valores nos logs).
- Repositório público ⇒ **logs das execuções também são públicos**. Por isso o código nunca imprime segredos e silencia o log de depuração do `urllib3`, que escreveria a URL do Telegram (com o token) por inteiro.
- `ci.yml` roda com `permissions: contents: read` (menor privilégio); só o `monitor.yml` tem `contents: write`, necessário para salvar o histórico.
- Próximo passo de maturidade: fixar as Actions por *commit SHA* em vez de tag (proteção contra cadeia de suprimentos).

---

## ADR-007 — "Mesmo produto" = mesmo código de fábrica

**Contexto.** Comparar preço entre lojas exige saber que dois anúncios são o mesmo aparelho; títulos variam muito.

**Decisão.** Cada modelo no `config.yaml` tem uma lista de códigos de fábrica (ex.: `42ezvca09m5`). Oferta cujo texto contém o código entra no grupo do modelo. Sem código ⇒ aparece no resumo, mas não participa do alerta de diferença.

**Consequência.** Zero falso positivo de "mesmo produto"; em troca, a lista de códigos precisa de manutenção manual (instruções no guia).
