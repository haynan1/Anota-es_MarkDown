# Markdown Studio

Aplicação web local para criar, organizar, versionar e exportar documentos em
Markdown. Roda inteiramente na sua máquina, sem nuvem, sem login e sem
depender de internet para funcionar.

---

## Índice

- [Funcionalidades](#funcionalidades)
- [Metas: a jornada](#metas-a-jornada)
- [As telas](#as-telas)
- [Arquitetura](#arquitetura)
- [Requisitos](#requisitos)
- [Instalação no Windows](#instalação-no-windows)
- [Instalação no Linux](#instalação-no-linux)
- [Configuração do .env](#configuração-do-env)
- [Banco de dados e migrations](#banco-de-dados-e-migrations)
- [Como iniciar a aplicação](#como-iniciar-a-aplicação)
- [Como executar os testes](#como-executar-os-testes)
- [Exportação em PDF](#exportação-em-pdf)
- [Ações em massa](#ações-em-massa)
- [Exportar e importar em massa](#exportar-e-importar-em-massa)
- [Copiar para a Wix](#copiar-para-a-wix)
- [Dependências do WeasyPrint](#dependências-do-weasyprint)
- [Backup e restauração](#backup-e-restauração)
- [Segurança](#segurança)
- [Cuidados para acesso por rede local](#cuidados-para-acesso-por-rede-local)
- [Solução de problemas](#solução-de-problemas)
- [Estrutura de diretórios](#estrutura-de-diretórios)
- [Limitações conhecidas](#limitações-conhecidas)
- [Melhorias futuras](#melhorias-futuras)

---

## Funcionalidades

**Escrita**
- Editor Markdown com pré-visualização ao vivo
- Três modos: dividido, somente editor, somente visualização
- Barra de ferramentas que insere sintaxe na posição do cursor
- **Alinhamento** do bloco onde está o cursor: esquerda, centralizado, direita
  ou justificado, pelo menu da barra ou pelo teclado (ver abaixo)
- Atalhos: `Ctrl+S` salvar, `Ctrl+P` exportar PDF, `Ctrl+B` negrito,
  `Ctrl+I` itálico, `Ctrl+K` link, `Tab` indentar, `Esc` sair
- Atalhos de alinhamento: `Ctrl+Shift+L` esquerda, `Ctrl+Shift+E` centralizado,
  `Ctrl+Shift+R` direita, `Ctrl+Shift+J` justificado
- Tela cheia, contadores de palavras e caracteres, tempo estimado de leitura

**Salvamento**
- Salvamento automático com debounce e intervalo configurável
- Indicador de estado: salvando, salvo, alterações pendentes, erro, conflito
- Rascunho local no `localStorage` com recuperação e comparação
- Controle de concorrência otimista: uma resposta atrasada nunca sobrescreve
  uma edição mais recente

**Arquivos do computador**
- Enviar qualquer arquivo arrastando, colando ou pelo botão de clipe da barra
- **Imagens e vídeos** entram no texto: PNG, JPG, GIF, WebP, MP4 e WebM
- **Anexos** viram um card com nome, tipo e tamanho, clicável para baixar:
  PDF, Word, Excel, PowerPoint, OpenDocument, RTF, EPUB, ZIP, 7Z, RAR, GZ,
  MP3, WAV, OGG e texto (TXT, MD, CSV, TSV, JSON, YAML, INI, LOG, RST)
- Fila de envio com progresso por arquivo, cancelar e tentar de novo
- Tipo determinado pelo conteúdo do arquivo, nunca pela extensão
- Anexos são sempre entregues como download, nunca interpretados pelo navegador
- Imagens entram no PDF; vídeos viram uma nota impressa; anexos viram uma
  linha "Anexo: nome — tipo · tamanho"

**Alinhamento de texto**
- Markdown não tem alinhamento próprio; aqui ele é escrito como um bloco
  cercado, a convenção do Pandoc:

  ```
  ::: centro
  Este parágrafo sai centralizado.

  E este também, no mesmo bloco.
  :::
  ```
- Palavras aceitas: `centro`, `direita`, `esquerda`, `justificado` — e os
  equivalentes em inglês (`center`, `right`, `left`, `justify`)
- Dentro do bloco continua sendo Markdown normal: títulos, listas, imagens,
  negrito, links
- Você não precisa digitar nada disso: o menu de alinhamento da barra envolve o
  bloco onde está o cursor, troca o alinhamento com um clique e desfaz ao
  escolher "esquerda" ou o alinhamento que já está ativo
- O ícone da barra mostra o alinhamento do bloco onde o cursor está
- Vale na visualização, no documento salvo e no PDF, nos quatro temas
- Uma palavra desconhecida (`::: aviso`) ou uma cerca sem fechamento aparecem
  como texto — o conteúdo nunca desaparece dentro de um bloco que você não pediu

**Ligações entre documentos**
- Sintaxe `[[Título do documento]]`, como no Obsidian
- `[[Título|texto alternativo]]` para personalizar o texto do link
- Busca sem diferenciar acentos, maiúsculas ou pontuação
- Link para documento inexistente fica destacado e, ao ser clicado, cria o documento

**Organização**
- **Grupos** — coleções de documentos do mesmo assunto, com cor e descrição.
  Um documento pode estar em vários grupos, e cada grupo guarda sua própria
  ordem (arraste ou use as setas). Adicione pelo editor, pela página do grupo
  ou selecionando vários documentos na lista.
- Categorias com cor e etiquetas livres
- Busca por título, conteúdo, categoria e etiqueta
- Filtros por grupo, categoria e etiqueta — incluindo **sem grupo** e **sem
  etiqueta**, as duas perguntas que uma lista de grupos não sabe fazer e que
  mostram o que o acervo está perdendo de vista; ordenação, paginação, cards
  ou lista
- Favoritos e arquivamento
- **Ações em massa**: mudar a categoria, agrupar, arquivar, proteger, baixar
  em `.md` ou mandar para a lixeira — para os documentos marcados **ou para
  todos os resultados do filtro atual**, não só os que cabem na página
- **Cadeado**: protege um documento contra exclusão acidental, inclusive ao
  esvaziar a lixeira. A edição continua livre — o cadeado protege a existência,
  não o conteúdo.

**Histórico**
- Versão criada apenas quando o conteúdo realmente muda (deduplicação por hash)
- Linha do tempo, visualização de versão, comparação com destaque de diferenças
- Restauração preservando o estado anterior

**Entrada e saída**
- Importar `.md` com validação, arrastar e soltar e prévia
- **Importar em massa**: vários arquivos de uma vez, ou um `.zip` inteiro
- Baixar o `.md` original em UTF-8
- **Exportar em massa**: todos os documentos (ou só os selecionados) num `.zip`
  de `.md`, em pastas por categoria, com metadados no topo de cada arquivo —
  o pacote volta pela importação sem perder nada
- Exportar PDF em 4 temas, A4 ou Carta — **apenas a representação do Markdown**,
  sem capa, cabeçalho, rodapé ou numeração
- **Copiar para a Wix** — o documento como texto formatado, pronto para colar
  na descrição do produto; com imagens no meio, ele vem dividido em partes,
  uma por vez, com a imagem a subir nomeada entre elas (ver abaixo)
- Backup completo em ZIP e restauração com mesclagem ou substituição

**Metas**
- Metas com data, horário, prioridade, categoria e situação
- Repetição: dias úteis, fins de semana, por uma quantidade de dias ou todos os
  dias — concluídas um dia de cada vez, sem que marcar a terça marque a quinta
- Metas sem prazo, que ficam no acervo até você trazê-las para hoje
- Esteira em três colunas com arrastar e soltar, atrasadas recolhidas acima e
  acervo sem prazo recolhido abaixo
- Plano da semana ou do mês, dia a dia, incluindo os dias livres
- Metas predefinidas: um molde guardado, ativado no dia que você escolher
- **Uma meta pode apontar para um documento daqui** — “terminar a proposta”
  abre a proposta
- XP, nível, sequência de dias, recorde e mais de oitenta conquistas
- Frases motivacionais de fábrica e suas próprias, com intervalo de troca
- As metas viajam no backup, com as exceções de cada dia

**Interface**
- Tema claro, escuro ou automático; cor principal configurável
- Responsiva: sidebar recolhível no desktop, navegação inferior no celular
- Acessibilidade: navegação por teclado, foco visível, ARIA, `prefers-reduced-motion`
- Nenhum estado comunicado apenas por cor

---

## Metas: a jornada

Um documento guarda o que você escreveu. Uma meta guarda o que você combinou
consigo mesmo — e as duas coisas se comportam de formas opostas, o que é o
motivo de as metas terem tabelas próprias em vez de virarem um tipo de
documento.

**Uma meta é uma regra, não uma linha por dia.** “Correr às terças e quintas
até o fim do mês” é uma meta com uma regra de repetição; os catorze dias em que
ela cai são calculados, não armazenados. O que fica gravado é a *exceção*: o
dia em que o estado fugiu do padrão da série. Um dia sem linha nenhuma é um dia
que ainda está como a série manda — e é isso que permite uma meta “todos os
dias, sem prazo final” existir sem criar milhares de linhas.

**Concluir é do dia, não da meta.** Uma série nunca nasce concluída: marcar a
série inteira concluiria terças que ainda nem chegaram. Cada dia é concluído
sozinho, e cada conclusão vale 10 XP — um hábito cumprido trinta vezes vale
trinta, não um.

**A meta olha para dentro da biblioteca.** `document_id` é o que faz esta
funcionalidade pertencer a *esta* aplicação: a missão aponta para o documento
de que ela trata, e abre com um clique. Apagar o documento tira o atalho, nunca
o compromisso.

**Tudo que se faz arrastando também se faz por um botão.** A esteira move
cartões com o ponteiro *e* com um formulário que recarrega a página — porque um
teclado, um leitor de tela e um navegador sem JavaScript precisam chegar aos
mesmos lugares.

### Como XP, nível e sequência são calculados

| Número | Regra |
|:-------|:------|
| **XP** | 10 por conclusão. Criar uma meta não vale nada: o prêmio é por fazer. |
| **Nível** | O nível N custa N × 150 XP. Subir fica mais difícil, sem teto. |
| **Sequência** | Dias seguidos com ao menos uma conclusão. O dia de hoje ainda não conta contra você: a sequência só quebra quando o dia passa em branco. |
| **Recorde** | A maior sequência que já existiu, mesmo que tenha terminado. |
| **Dias produtivos** | Quantos dias distintos tiveram alguma conclusão. |

Nada disso é armazenado — tudo é lido das conclusões. Um contador de XP numa
coluna criaria uma segunda verdade, que passa a divergir na primeira meta
apagada.

### O que ficou de fora, e por quê

Esta funcionalidade nasceu de um projeto separado que tinha contas de usuário,
plano Premium e lembretes. Nada disso veio junto, de propósito:

- **Contas, login e recuperação de senha.** Esta aplicação roda na sua máquina
  e tem um dono só. Uma tela de login não protegeria nada e cobraria de todo
  mundo.
- **Plano Premium e o limite de 5 metas.** Um paywall dentro de um programa
  local é cenário, não funcionalidade. Tudo que o Premium liberava — frases
  personalizadas, intervalo de troca, relatórios — está disponível.
- **Lembretes.** No projeto de origem havia a tabela e o agendador, mas nenhuma
  tarefa registrada: era um interruptor que não acendia nada. O que ficou no
  lugar é o que já funciona — as atrasadas recolhidas na esteira e a sequência
  em risco visível no painel.
- **Foto de perfil e alternância pt-BR/en-US.** Sem contas não há perfil, e a
  interface inteira é em português.

---

## As telas

| Tela | Rota | O que faz |
|:-----|:-----|:----------|
| **Painel** | `/` | Total de documentos, palavras escritas, favoritos, arquivados, últimos modificados, categorias mais usadas |
| **Documentos** | `/documentos/` | Lista ou cards, busca, filtros por categoria/grupo/etiqueta/favorito (com “sem grupo” e “sem etiqueta”), ordenação, paginação, ações rápidas e ações em massa sobre a seleção ou sobre todos os resultados |
| **Editor** | `/editor/<uuid>` | Título, editor, pré-visualização ao vivo, barra de ferramentas, painel de organização, exportações |
| **Histórico** | `/documentos/<uuid>/historico` | Linha do tempo das versões, visualização, comparação e restauração |
| **Lixeira** | `/lixeira/` | Documentos excluídos, restauração, exclusão definitiva e esvaziamento com confirmação forte |
| **Grupos** | `/grupos/` | Criar grupos, ver quantos documentos cada um reúne |
| **Grupo** | `/grupos/<uuid>` | Documentos do grupo na ordem definida, reordenar, adicionar e remover |
| **Categorias** | `/documentos/categorias` | Criar e remover categorias e etiquetas, com contagem de uso |
| **Importar** | `/documentos/importar` | Um arquivo, vários de uma vez ou um `.zip`, por seleção ou arrastar e soltar, com prévia antes de salvar e relatório depois; e a exportação de todo o acervo em Markdown |
| **Metas** | `/metas/` | O acervo inteiro, com filtros por situação, prioridade e categoria, e a faixa de progresso |
| **Esteira** | `/metas/esteira` | As metas de hoje em três colunas, com arrastar e soltar, atrasadas e acervo sem prazo |
| **Plano** | `/metas/plano` | A semana ou o mês, dia a dia, incluindo os dias livres |
| **Predefinidas** | `/metas/predefinidas` | Moldes guardados, ativados no dia que você escolher |
| **Histórico da jornada** | `/metas/historico` | Conclusões por dia, peso de cada categoria e a conta por trás dos números |
| **Conquistas** | `/metas/conquistas` | O catálogo inteiro, com o que já foi desbloqueado e quando |
| **Frases** | `/metas/frases` | Frases motivacionais, intervalo de troca e o recomeço da jornada |
| **Configurações** | `/configuracoes/` | Aparência, PDF, fuso horário, autosave, backups e manutenção |

---

## Arquitetura

Application factory + blueprints, com três camadas bem separadas:

```
blueprints/    HTTP: traduzem requisição → serviço → resposta. Sem regra de negócio.
services/      Regras de negócio. Não importam Flask views; testáveis isoladamente.
repositories/  Construção de consultas. Índices e eager loading vivem aqui.
models/        Mapeamento SQLAlchemy 2.0.
```

**Decisões que valem explicação:**

- **Um único renderizador de Markdown.** A pré-visualização ao vivo chama o
  servidor (`POST /api/preview`) em vez de renderizar no navegador. Assim o que
  você vê enquanto escreve é exatamente o que é salvo e impresso — não existe
  uma segunda implementação de Markdown para divergir, nem uma segunda
  superfície de sanitização para auditar.

- **CSP sem `unsafe-inline`.** A aplicação não emite nenhum script nem estilo
  inline. O Python-Markdown gera `style="text-align:…"` em células de tabela; o
  sanitizador reescreve isso como classe CSS. A cor de destaque escolhida pelo
  usuário é servida como folha de estilo em `/assets/theme.css`. Cores de
  categoria chegam como `data-color` e são aplicadas via CSSOM pelo JavaScript,
  o que a CSP não restringe.

- **Concorrência otimista por número de revisão.** Cada salvamento envia a
  `revision` que o cliente viu por último. Se não bater, o servidor responde
  `409` em vez de sobrescrever. É mais confiável que comparar `updated_at`,
  cuja resolução de tempo pode empatar.

- **Deduplicação de versões por hash.** O SHA-256 de `título + corpo` é
  comparado com a última versão. O autosave disparando a cada poucos segundos
  não polui o histórico.

- **Dois índices de busca, porque "achar o documento" são duas perguntas.**
  Um FTS5 sobre título e corpo com `remove_diacritics 2` responde por palavra,
  sem diferenciar acentos nem maiúsculas — "codigo" encontra "código". Um
  segundo FTS5, só de títulos e com o tokenizador `trigram`, responde por
  *trecho*: "trofia" encontra "Hipertrofia". Um índice de palavras não teria
  como fazer isso — ele conhece palavras inteiras e seus começos, nunca o meio
  delas. Nenhum caractere digitado pelo usuário é interpretado como sintaxe do
  FTS: a expressão `MATCH` é reconstruída a partir de tokens ou citada inteira,
  com aspas escapadas e caracteres de controle descartados.

- **A relevância pondera o título dez vezes acima do corpo.** `bm25` sem pesos
  trata as duas colunas por igual, e então buscar um título devolvia todos os
  documentos que o mencionam de passagem com o documento em si perdido no meio
  — o que fazia a busca parecer exigir o título completo. Uma busca sem ordem
  escolhida é ordenada por relevância, não por data.

- **Degradação em camadas.** Sem o tokenizador `trigram` (SQLite anterior ao
  3.34), a busca por palavra continua e só os trechos no meio de uma palavra
  deixam de ser encontrados. Sem FTS5, tudo cai para `LIKE` parametrizado.

- **Dois motores de PDF atrás de uma interface.** WeasyPrint quando disponível,
  xhtml2pdf sempre. Ver [Dependências do WeasyPrint](#dependências-do-weasyprint).

- **Sem bibliotecas externas por CDN.** Ícones são um sprite SVG próprio, o CSS
  é escrito à mão e as fontes usam a pilha do sistema. A aplicação funciona sem
  internet.

---

## Requisitos

- Python 3.11 ou superior (desenvolvida e testada em 3.13)
- ~80 MB de espaço para o ambiente virtual
- Nenhum servidor de banco de dados: o SQLite é embutido

---

## Instalação no Windows

```powershell
cd C:\caminho\para\MarkDown_Projetos

python -m venv venv
.\venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt

copy .env.example .env
```

> Se o PowerShell bloquear a ativação, execute uma vez:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

Gere uma chave secreta e cole no `.env`:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## Instalação no Linux

```bash
cd /caminho/para/MarkDown_Projetos

python3 -m venv venv
source venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## Configuração do .env

Copie `.env.example` para `.env` e ajuste. Os valores que mais importam:

| Variável | Padrão | Para que serve |
|:---------|:-------|:---------------|
| `SECRET_KEY` | gerada em memória | Assina sessões e tokens CSRF. **Obrigatória em produção.** |
| `FLASK_ENV` | `development` | `development`, `production` ou `testing` |
| `HOST` | `127.0.0.1` | Interface de escuta. Ver [rede local](#cuidados-para-acesso-por-rede-local) |
| `PORT` | `5001` | Porta HTTP |
| `PDF_ENGINE` | `auto` | `auto`, `weasyprint` ou `xhtml2pdf` |
| `MAX_UPLOAD_MB` | `8` | Teto de um envio avulso de documento; ultrapassar gera a página 413 |
| `MAX_BULK_IMPORT_MB` | `64` | Teto de um envio de importação em massa (vários `.md` ou um `.zip`) |
| `MEDIA_MAX_IMAGE_MB` | `10` | Teto de uma imagem enviada pelo editor |
| `MEDIA_MAX_VIDEO_MB` | `100` | Teto de um vídeo enviado pelo editor |
| `MEDIA_MAX_FILE_MB` | `50` | Teto de um anexo (PDF, Office, ZIP, texto) |
| `MAX_MARKDOWN_MB` | `2` | Tamanho máximo do corpo de um documento |
| `BACKUP_DIR` | `instance/backups` | Onde os ZIPs de backup são gravados |
| `AUTO_CREATE_DB` | `true` | Cria tabelas ausentes ao iniciar |

O arquivo `.env` está no `.gitignore`. Nunca versione uma chave real.

---

## Banco de dados e migrations

O caminho canônico é o Alembic:

```bash
# Windows: set FLASK_APP=run.py
export FLASK_APP=run.py

flask db upgrade
```

Isso cria `instance/app.db` com todas as tabelas e índices. As tabelas virtuais
FTS5 da busca são criadas automaticamente na primeira execução da aplicação —
e o índice de títulos é preenchido no mesmo boot se o acervo já existia, sem
migration nem reindexação manual.

Se você alterar os modelos:

```bash
flask db migrate -m "descrição da mudança"
flask db upgrade
```

> Ao gerar uma migration nova, defina `AUTO_CREATE_DB=0` para que o Alembic
> consiga comparar contra um banco vazio.

**Comandos auxiliares:**

```bash
flask init-db          # cria as tabelas sem usar migrations
flask seed-welcome     # cria um documento de exemplo (pode apagar depois)
flask reindex          # reconstrói o índice de busca
flask backup           # gera um backup ZIP
flask pdf-engine       # mostra qual motor de PDF está ativo nesta máquina
```

---

## Como iniciar a aplicação

```bash
python run.py
```

Abra <http://127.0.0.1:5001>.

Atalhos prontos: `start.bat` ou `start.ps1` no Windows, `start.sh` no Linux.
Eles criam o ambiente virtual, instalam dependências, aplicam migrations e
sobem o servidor.

```powershell
.\start.ps1
```

```bash
chmod +x start.sh
./start.sh
```

> **A porta 5001 já pode estar ocupada.** Se outra aplicação estiver usando,
> defina `PORT=5050` no `.env`.

> `python run.py` usa o servidor de desenvolvimento do Flask. Ele é adequado
> para uso local por uma pessoa e **não** deve ser usado como servidor de
> produção exposto a uma rede.

---

## Como executar os testes

```bash
pytest
```

Com relatório de cobertura:

```bash
pytest --cov=app --cov-report=term-missing
```

Um arquivo específico:

```bash
pytest tests/test_markdown.py -v
```

Os testes usam um banco SQLite temporário por teste, então rodá-los nunca
toca nos seus documentos reais.

---

## Exportação em PDF

Pelo editor: botão da impressora, ou `Ctrl+P` (que abre o diálogo de exportação
em vez da impressão do navegador).

Dois formatos, escolhidos no diálogo de exportação:

- **Documento formatado** — a representação do Markdown, e nada além disso:
  títulos, tabelas, listas e código renderizados. Sem capa, sem linha de
  metadados, sem cabeçalho, sem rodapé e sem numeração de página. O que aparece
  na folha é o que o documento diz; um título no PDF vem de um `# Título` no
  Markdown, não de um bloco que o aplicativo acrescenta.
- **Código-fonte Markdown** — listagem numerada do texto original, útil para
  revisar a sintaxe ou arquivar o fonte. Este continua se identificando no
  topo: é uma listagem, não o documento.

- **Tamanhos:** A4 e Carta (Letter)
- **Temas:** Clássico, Minimalista, Acadêmico e Moderno — tipografia apenas.
  Um tema decide como o Markdown é composto, nunca o que é acrescentado a ele.
- **Configurável:** margens e fonte (em Configurações)
- **Nome do arquivo:** derivado do slug do título, sem caracteres inválidos —
  `guia-profissional-de-google-ads.pdf`

---

## Ações em massa

Marque documentos na lista e uma barra sobe do rodapé com tudo o que dá para
fazer com eles de uma vez:

| Ação | O que faz |
| --- | --- |
| **Categoria → Mover** | Coloca a seleção inteira numa categoria — ou em *Sem categoria*, que aqui é um destino como outro qualquer, não a ausência de escolha |
| **Grupo → Agrupar** | Acrescenta a seleção ao fim de um grupo. Quem já estava lá não entra duas vezes |
| **Baixar .md** | Empacota a seleção num `.zip` (ver [Exportar e importar em massa](#exportar-e-importar-em-massa)) |
| **Arquivar / Desarquivar** | Tira da lista principal sem excluir; o botão acompanha o filtro em que você está |
| **Proteger / Remover proteção** | Liga e desliga o cadeado |
| **Mover para a lixeira** | Pergunta antes. Documentos com cadeado são ignorados e a página diz quantos |

Nenhuma delas apaga nada de imediato: a lixeira é reversível e todo o resto é
um estado que se desfaz pelo mesmo caminho.

### Selecionar além da página

*Selecionar todos nesta página* marca o que está à vista. Quando a página
inteira está marcada e existe mais resultado do que ela mostra, aparece a
oferta que interessa de verdade:

> Só esta página está selecionada. **Selecionar todos os 348 resultados**

A partir daí a ação vale para **todos os documentos que o filtro atual
encontra** — busca, categoria, grupo, etiqueta, favoritos e escopo, exatamente
como estão na tela. O navegador não envia 348 identificadores: envia os
filtros, e o servidor refaz a mesma consulta que desenhou a página. Isso
significa que uma seleção de 4 documentos e uma de 400 custam a mesma
requisição, e que não existe seleção possível fora do que a tela já mostrava —
a lixeira, por exemplo, continua inalcançável, porque nenhum filtro da lista
chega até ela.

Dois tetos, por dois motivos diferentes:

| Seleção | Teto | Por quê |
| --- | --- | --- |
| Caixas marcadas | 200 | É o que uma requisição carrega de identificadores |
| Todos os resultados | 2 000 | É quanto uma ação deve tocar de uma vez sem você ter visto passar |

Quando o filtro encontra mais do que o teto, a oferta já diz isso antes do
clique (*“Selecionar os 2.000 primeiros de 3.410 resultados”*) e a página avisa
de novo depois. Uma ação em massa nunca para no meio em silêncio.

---

## Exportar e importar em massa

Todo o acervo entra e sai em Markdown, sem passar pelo formato de backup.

**Exportar** — botão *Exportar tudo* na tela de Documentos (ou na tela de
Importar), e *Baixar .md* na barra de seleção múltipla para levar só o que está
marcado. O `.md` da barra segue a mesma seleção das outras ações em massa: se
ela estiver em *todos os resultados*, o pacote traz o filtro inteiro, não só a
página. Sai um `.zip` com:

```
documentos-markdown-20260811-153000.zip
├── guia-sem-categoria.md
└── marketing-digital/
    ├── guia-de-google-ads.md
    └── plano-de-midia.md
```

Uma pasta por categoria, um arquivo por documento, e no topo de cada arquivo um
bloco de front matter com o que o Markdown sozinho não guarda:

```markdown
---
title: "Guia de Google Ads"
uuid: "3f2b1c9d-…"
slug: "guia-de-google-ads"
category: "Marketing Digital"
tags: ["ads", "google"]
groups: ["Campanhas 2026"]
favorite: true
created: "2026-01-02T12:00:00+00:00"
updated: "2026-03-14T09:30:00+00:00"
---

# Guia de Google Ads
…
```

A lixeira fica de fora — aqueles documentos foram excluídos, e um pacote que os
ressuscitasse na próxima importação seria uma surpresa. Para levar tudo,
inclusive a lixeira e o histórico de versões, use o backup completo.

Um pacote leva até 20 000 documentos (teto aplicado como `LIMIT` na consulta,
não como corte depois de carregar tudo). Acima disso o log registra um aviso de
que o pacote pode não conter a biblioteca inteira — o número existe para que
uma base descontrolada não gere uma resposta sem fim, não como limite de uso.

**Importar** — a mesma tela de sempre (`/documentos/importar`), agora aceitando
vários arquivos de uma vez ou um `.zip` com eles dentro. O bloco de front
matter é lido de volta: categorias, etiquetas, grupos, favoritos, arquivados e
datas voltam como estavam.

- **Reimportar o mesmo pacote não duplica nada.** Cada documento é reconhecido
  pelo `uuid` gravado no arquivo; o que já existe é contado como "já existente"
  e mantido intacto.
- **Um arquivo com problema não derruba o lote.** Codificação errada, entrada
  corrompida, cabeçalho malformado: cada caso é contado, descrito no relatório
  ao fim da importação, e a importação segue.
- **Arquivos de outras ferramentas funcionam.** Sem front matter, o título vem
  do primeiro `#` ou do nome do arquivo. Chaves desconhecidas (`aliases`,
  `draft`) são ignoradas sem reclamar.
- **O ZIP é tratado como hostil**: caminhos com `..`, entradas absolutas,
  número de membros e tamanho descompactado são verificados antes de ler um
  único byte.

---

## Copiar para a Wix

A descrição de um produto na Wix é um campo de **texto rico**: ele aceita texto
com formatação aplicada (negrito, itálico, títulos, listas, links), como quando
se cola de um editor de textos. Markdown colado ali entra literal, com os
asteriscos à mostra; HTML colado também não é interpretado.

O botão de copiar no editor (ícone de duas folhas, ao lado da impressora)
resolve isso: ele converte o documento para o subconjunto que o campo aceita,
mostra exatamente o que será colado e coloca no clipboard nos dois formatos que
um editor de textos usaria (`text/html` e `text/plain`). Depois é só
<kbd>Ctrl</kbd>+<kbd>V</kbd> na Wix.

**O que é convertido, e não perdido:**

| No documento | Ao colar na Wix |
|:-------------|:----------------|
| Tabela | Um parágrafo por linha, com o título da coluna em negrito |
| Bloco de código | Parágrafos de texto comum, com a indentação preservada |
| Checklist | Os símbolos ☐ e ☑ |
| Linha horizontal | Uma linha de traços |
| Lista de definições | Termo em negrito, definição no parágrafo seguinte |

**Copiar em partes, quando há imagens.** A Wix aceita uma imagem por vez, pela
galeria do produto — então um documento com imagens não é uma colagem só, é uma
rotina: cola, sobe a imagem, volta. Quando o documento tem imagens ou vídeos
entre os textos, o diálogo vira o roteiro dessa rotina: um cartão por trecho de
texto, o arquivo a subir nomeado entre eles (`Agora suba na Wix: imagem:
frente.png`) e um contador de quantas partes já foram copiadas. A parte em que
você parou fica destacada, e as já copiadas ficam marcadas — inclusive se você
fechar e reabrir o diálogo, desde que o texto não tenha mudado. O botão
"Copiar tudo de uma vez" continua ali para quem preferir.

Nada se perde no corte: as partes, emendadas, são exatamente a cópia inteira —
e isso é um teste, não uma promessa.

**O que não atravessa** — e é sempre informado na tela antes de copiar:
imagens e vídeos (a Wix não aceita mídia colada de fora; use a galeria do
produto), anexos (o arquivo continua aqui, só o nome vai junto), links
internos, que só funcionam dentro deste aplicativo e viram texto, e o
alinhamento, que a Wix descarta ao colar — o texto vai inteiro, alinhado à
esquerda, e você reposiciona com os botões do editor dela.

> A cópia usa a API de clipboard do navegador, disponível em contextos seguros
> — `localhost` é um deles. Se você acessar o app pelo IP da máquina em HTTP,
> a cópia cai automaticamente para a seleção do próprio navegador, com o mesmo
> resultado.

---

## Dependências do WeasyPrint

O WeasyPrint produz PDFs melhores: layout CSS real, cabeçalhos e rodapés em
margin boxes, numeração via contadores e links clicáveis. Mas ele depende de
bibliotecas nativas (GTK, Pango, Cairo) que **não vêm com o `pip install` no
Windows**.

A aplicação lida com isso sozinha: se o WeasyPrint não puder ser importado, ela
usa o **xhtml2pdf** (ReportLab, Python puro, sem dependências nativas). Você
não precisa fazer nada — a exportação funciona de qualquer forma, com fidelidade
visual menor.

Verifique o que está ativo na sua máquina:

```bash
flask pdf-engine
```

A tela de Configurações também mostra o motor ativo.

**Para habilitar o WeasyPrint:**

*Windows* — instale o GTK3 Runtime e reinicie o terminal:
<https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases>

*Debian / Ubuntu:*
```bash
sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi-dev
```

*Fedora:*
```bash
sudo dnf install pango harfbuzz
```

*macOS:*
```bash
brew install pango libffi
```

Depois de instalar, `PDF_ENGINE=auto` passa a usar o WeasyPrint sozinho.

**Diferenças do motor alternativo (xhtml2pdf):**
- Cabeçalho e rodapé usam frames fixos em vez de margin boxes
- Suporte a CSS reduzido: sem flexbox, grid ou `break-inside`
- Fontes limitadas às 14 padrão do PDF (Helvetica, Times, Courier)
- Sem controle fino de órfãs e viúvas

---

## Backup e restauração

**Criar:** Configurações → Cópias de segurança → *Criar backup*. Ou `flask backup`.

O ZIP contém:

```
manifest.json          versão do formato, versão da aplicação, data e contagens
data.json              documentos, versões, categorias, etiquetas, grupos,
                       configurações, metas (com as exceções de cada dia),
                       predefinidas, frases e conquistas
documents/*.md         cópias legíveis em Markdown puro
```

Os arquivos `.md` tornam o backup útil mesmo sem esta aplicação.

A jornada viaja junto de propósito: um backup que salva os documentos e perde
as metas restaura metade da aplicação — e a metade perdida é justamente a que
não se reconstrói relendo os `.md` do arquivo. Metas apontam para documentos
pelo UUID, e não pelo id, para que a ligação sobreviva a uma restauração em
outra instalação.

**Restaurar:** Configurações → *Restaurar a partir de um arquivo*, em um de dois
modos:

- **Mesclar** — adiciona apenas o que ainda não existe (documentos, metas,
  predefinidas, frases e conquistas, cada um pelo seu identificador). Nada é
  apagado.
- **Substituir** — apaga tudo e restaura o backup. Exige digitar `SUBSTITUIR`
  e **cria automaticamente um backup de segurança antes** de qualquer remoção.

Todo backup é validado antes da restauração: formato do manifesto, versão
suportada, estrutura do payload, ausência de caminhos com `..` e limite de
tamanho descompactado (proteção contra zip bomb).

A aplicação mantém os N backups mais recentes (padrão 10, ajustável).

---

## Segurança

Mesmo sem login, a aplicação trata o conteúdo dos documentos como não confiável:

| Área | Medida |
|:-----|:-------|
| **XSS** | Sanitização por allowlist (Bleach) em todo HTML renderizado. `<script>`, `<style>`, `<iframe>`, `<form>` e handlers `on*` são removidos com seu conteúdo |
| **URLs** | Apenas `http`, `https`, `mailto` e `tel`. `javascript:`, `vbscript:`, `file:` e `data:` são bloqueados |
| **Formulários** | Apenas `<input type="checkbox">` de checklist sobrevive; qualquer outro controle é descartado |
| **CSP** | Estrita, sem `unsafe-inline` nem `unsafe-eval`, com `frame-ancestors 'none'`, `base-uri 'none'`, `object-src 'none'` |
| **Headers** | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-*` |
| **CSRF** | Flask-WTF em todos os formulários e endpoints JSON (via header `X-CSRFToken`) |
| **SQL** | Exclusivamente pelo ORM com parâmetros vinculados; a expressão FTS é reconstruída a partir de tokens |
| **Upload** | Extensão, tamanho, codificação UTF-8 e detecção de binário disfarçado |
| **Arquivos** | Nomes de download reconstruídos a partir do slug; membros de ZIP validados contra path traversal |
| **SSRF** | A geração de PDF **nunca** faz requisições de rede. Um único ponto de resolução restringe recursos a `static/` e a imagens enviadas, resolvidas pelo banco |
| **Upload** | Tipo decidido por assinatura de bytes, não por extensão nem pelo MIME do cliente. SVG é recusado (formato que carrega script). Nome de arquivo gerado; nada da requisição toca o sistema de arquivos |
| **Entrega de mídia** | Caminho lido do banco, `Content-Type` reproduzido da nossa allowlist, `nosniff` e CSP `sandbox` na resposta |
| **Anexos** | Só imagem e vídeo são exibidos inline. PDF, Office, ZIP e texto são sempre entregues com `Content-Disposition: attachment` — nada que o navegador possa interpretar dentro da nossa origem |
| **Texto sem assinatura** | `.txt`, `.csv`, `.json` e afins não têm bytes mágicos: são aceitos só se decodificarem em UTF-8 sem bytes nulos, e a extensão apenas escolhe o rótulo, entre uma lista curta que exclui `.html`, `.svg`, `.js`, `.bat` e outros executáveis |
| **Texto rico (Wix)** | O HTML copiado passa por uma segunda sanitização, com allowlist menor que a da aplicação: só formatação e links `http(s)`/`mailto`/`tel` |
| **Vídeo no conteúdo** | Só sobrevive apontando para `/midia/`; `autoplay` é removido e `controls` é forçado |
| **Erros** | Página amigável para o usuário, stack trace apenas no log |
| **Logs** | Registram estrutura, nunca conteúdo de documentos |

**Sobre imagens remotas:** no navegador elas carregam normalmente (é uma
requisição do cliente, o mesmo risco de qualquer página web). Durante a geração
do PDF elas são **bloqueadas**, porque ali o download aconteceria no servidor e
seria um vetor de SSRF — um documento poderia forçar requisições para
`169.254.169.254` ou para serviços internos. Documentos com imagens remotas
exportam normalmente, apenas sem a imagem.

---

## Cuidados para acesso por rede local

**Esta aplicação não tem autenticação.** Qualquer pessoa que alcance a porta lê,
edita e apaga todos os seus documentos.

Por isso o padrão é `HOST=127.0.0.1` — apenas o seu próprio computador.

Se você realmente precisa acessar de outro dispositivo na mesma rede Wi-Fi,
entenda o que está aceitando:

- Todos na rede (incluindo visitantes e dispositivos comprometidos) terão acesso
  total, sem senha
- Redes públicas ou compartilhadas tornam isso equivalente a publicar seus
  documentos
- Não faça isso em rede corporativa sem falar com quem cuida da segurança

Se ainda assim for necessário, defina `HOST=0.0.0.0` no `.env`. A aplicação
grava um aviso no log ao iniciar nessa configuração. Prefira restringir por
firewall e **nunca** exponha a porta à internet.

---

## Solução de problemas

**`ModuleNotFoundError` ao iniciar**
O ambiente virtual não está ativo. Rode `.\venv\Scripts\Activate.ps1` (Windows)
ou `source venv/bin/activate` (Linux).

**`Address already in use` / a página abre outro sistema**
Outra aplicação ocupa a porta 5001. Defina `PORT=5050` no `.env`.
Para descobrir quem está usando:
```powershell
Get-NetTCPConnection -LocalPort 5001 -State Listen
```
```bash
lsof -i :5001
```

**`WeasyPrint could not import some external libraries`**
Esperado no Windows sem GTK. A aplicação usa o motor alternativo sozinha; não é
um erro fatal. Ver [Dependências do WeasyPrint](#dependências-do-weasyprint).

**`no such table: documents`**
Rode `flask db upgrade` (com `FLASK_APP=run.py` definido).

**A busca não encontra nada que deveria**
Reconstrua o índice: Configurações → *Reconstruir índice*, ou `flask reindex`.

**A pré-visualização não atualiza**
Ela depende de uma chamada ao servidor. Verifique o console do navegador; se
houver erro de CSRF, recarregue a página para obter um token novo.

**"Conflito de edição"**
O documento foi alterado em outra aba ou janela. Recarregue a página para ver a
versão atual — seu texto continua salvo no rascunho local.

**Perdi trabalho ao fechar o navegador**
Reabra o documento: se houver rascunho local mais recente, a aplicação oferece
recuperar, comparar ou ignorar.

**`SECRET_KEY must be set` em produção**
Defina `SECRET_KEY` no `.env` com um valor gerado por
`python -c "import secrets; print(secrets.token_urlsafe(48))"`.

---

## Estrutura de diretórios

```
MarkDown_Projetos/
├── app/
│   ├── __init__.py              application factory
│   ├── config.py                configuração por ambiente
│   ├── extensions.py            db, migrate, csrf + pragmas do SQLite
│   ├── security.py              CSP e headers de segurança
│   ├── errors.py                handlers de erro
│   ├── cli.py                   comandos flask personalizados
│   ├── models/                  Document, DocumentVersion, Category, Tag,
│   │                            Group, MediaAsset, MindMap, Setting,
│   │                            Goal, GoalOccurrence, GoalTemplate,
│   │                            MotivationalPhrase, AchievementUnlock
│   ├── services/                markdown, sanitizer, align, attachment,
│   │                            document, group, history, search, pdf, media,
│   │                            wix, import, backup, settings, mind_map*,
│   │                            goal_schedule, goal, progress, achievement*,
│   │                            phrase
│   ├── repositories/            document, version, taxonomy, group, mind_map,
│   │                            goal, achievement, phrase
│   ├── blueprints/              dashboard, documents, groups, goals, editor,
│   │                            history, trash, exports, settings, media,
│   │                            mindmaps, api
│   ├── templates/
│   │   ├── base.html
│   │   ├── components/          sidebar, topbar, macros, sprite, toasts, charts
│   │   ├── dashboard/ documents/ editor/ goals/ history/ mindmaps/ trash/
│   │   ├── settings/ errors/
│   │   └── pdf/                 document.html + document_fallback.html
│   └── static/
│       ├── css/                 base, markdown, editor, charts, mindmap, goals
│       ├── js/                  app, editor, mindmap, goals + modules/
│       └── favicon.svg
├── instance/                    app.db, backups/, exports/, logs/  (não versionado)
├── migrations/                  Alembic
├── tests/                       1770 testes (js/ roda sob o Node, se houver)
├── .env.example
├── .gitignore
├── requirements.txt
├── pytest.ini
├── run.py
├── start.bat / start.ps1 / start.sh
├── LICENSE
└── README.md
```

---

## Limitações conhecidas

- **WeasyPrint exige bibliotecas nativas.** Sem elas o PDF sai pelo xhtml2pdf,
  com fidelidade visual menor. Documentado acima.
- **Sem autenticação.** É uma decisão desta primeira versão, não um esquecimento.
  Por isso o padrão é escutar apenas em `127.0.0.1`.
- **Imagens remotas não entram no PDF.** Decisão de segurança contra SSRF.
- **A pré-visualização precisa do servidor.** Renderizar no cliente exigiria uma
  segunda implementação de Markdown e uma segunda superfície de sanitização.
- **Um usuário por vez.** O controle de concorrência protege contra sobrescrita
  entre abas, mas não há edição colaborativa.
- **FTS5 depende do build do SQLite.** Se ausente, a busca cai para `LIKE` — que
  funciona, mas diferencia acentos. O tokenizador `trigram`, que encontra
  trechos no meio de uma palavra, exige SQLite 3.34 (e 3.45 para ignorar
  acentos); sem ele a busca por palavra continua inteira. **Configurações**
  informa o que este build oferece.
- **A busca por trecho cobre títulos, não corpos.** Um índice de trigramas
  custa cerca de três entradas por caractere: sobre o corpo de um acervo de
  Markdown ele multiplicaria o banco para responder uma pergunta que ninguém
  faz de um corpo.
- **Sem testes de navegador.** O JavaScript foi validado manualmente e por
  verificações de integração; não há Playwright ou Selenium.
- **Os arquivos enviados não entram no backup.** O ZIP guarda documentos,
  histórico, taxonomias, grupos e configurações — as imagens, vídeos e anexos
  ficam em `instance/uploads/`. Copie essa pasta junto ao restaurar em outra
  máquina.
- **Áudio enviado é anexo, não toca na página.** MP3, WAV e OGG são aceitos e
  baixáveis; não há player embutido no documento.

---

## Melhorias futuras

- Sincronização entre dispositivos
- Login opcional (a arquitetura já isola as camadas para isso)
- Compartilhamento de documentos por link
- Modelos de documento prontos
- Sumário automático a partir dos títulos
- Exportação para DOCX e HTML
- Integração com armazenamento em nuvem
- Instalação como PWA
- Criptografia opcional dos backups

---

## Licença

MIT. Ver [LICENSE](LICENSE).
