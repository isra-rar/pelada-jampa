# PRD --- Aplicativo de Peladas

**Nome provisório:** Pelada+\
**Versão:** 1.0\
**Status:** Documento inicial de produto\
**Plataforma:** Aplicação web responsiva / mobile-first\
**Idioma inicial:** Português (Brasil)

------------------------------------------------------------------------

## 1. Visão geral

O Pelada+ será uma aplicação para organizar partidas de futebol amador,
administrar grupos de jogadores, controlar confirmações e pagamentos,
avaliar o desempenho dos participantes e realizar sorteios inteligentes
de equipes.

A proposta vai além de uma agenda de partidas: cada jogador terá um
perfil esportivo com atributos técnicos, uma classificação por nível
(Tier) e uma habilidade especial. Essas informações alimentarão um
algoritmo de formação de times, cujo objetivo será produzir equipes
equilibradas e partidas mais competitivas.

### 1.1. Problemas que o produto resolve

-   Dificuldade para saber quem participará de cada pelada.
-   Confirmações e desistências sem controle centralizado.
-   Falta de organização da lista de espera.
-   Controle manual de pagamentos e dívidas de partidas anteriores.
-   Discussões e desequilíbrio na divisão dos times.
-   Ausência de histórico de desempenho dos jogadores.
-   Dependência de grupos de mensagens e planilhas.

### 1.2. Objetivos do produto

1.  Simplificar a criação e administração de peladas recorrentes ou
    avulsas.
2.  Centralizar confirmações, presença e lista de espera.
3.  Criar perfis esportivos com atributos mensuráveis.
4.  Permitir avaliações pós-partida com prazo configurável.
5.  Controlar pagamentos e pendências por jogador.
6.  Montar equipes equilibradas considerando Tier, média, atributos e
    habilidades.
7.  Manter histórico de partidas, avaliações, sorteios e pagamentos.

### 1.3. Público-alvo

-   Organizadores de peladas.
-   Jogadores amadores.
-   Grupos recorrentes de futebol society, futsal ou campo.
-   Comunidades esportivas e quadras que organizam partidas.

------------------------------------------------------------------------

## 2. Perfis de usuário e permissões

O sistema terá três perfis principais:

  -----------------------------------------------------------------------
  Perfil                              Responsabilidades
  ----------------------------------- -----------------------------------
  Administrador da pelada             Cria partidas, gerencia jogadores,
                                      confirmações, pagamentos,
                                      avaliações e sorteios

  Jogador                             Participa de peladas, confirma
                                      presença, consulta perfil, paga e
                                      avalia outros jogadores

  Administrador da plataforma         Gerencia usuários, grupos,
                                      configurações globais e suporte
  -----------------------------------------------------------------------

Um mesmo usuário poderá participar de várias peladas e exercer papéis
diferentes em cada grupo.

### 2.1. Permissões do administrador da pelada

-   Criar, editar, cancelar e consultar peladas.
-   Convidar, adicionar, remover e inativar jogadores.
-   Definir outros administradores.
-   Configurar vagas, valores e prazos.
-   Consultar confirmações e lista de espera.
-   Registrar presença.
-   Registrar e corrigir pagamentos, mantendo auditoria.
-   Acompanhar avaliações.
-   Executar, repetir, editar e publicar sorteios.
-   Consultar relatórios do grupo.

### 2.2. Permissões do jogador

-   Criar e editar dados básicos do próprio perfil.
-   Escolher posição preferida e habilidade especial.
-   Entrar em uma pelada por convite.
-   Confirmar ou recusar presença.
-   Cancelar confirmação dentro das regras.
-   Consultar seus atributos, média e Tier.
-   Avaliar os participantes das partidas em que esteve presente.
-   Consultar seu histórico de partidas e pagamentos.

------------------------------------------------------------------------

## 3. Gestão de peladas

### 3.1. Cadastro de pelada

O administrador poderá criar uma pelada informando:

-   Nome.
-   Descrição.
-   Modalidade: society, futsal, campo ou outra.
-   Local e endereço.
-   Data e horário de início.
-   Horário previsto de término.
-   Duração.
-   Valor por jogador.
-   Quantidade de vagas.
-   Quantidade de jogadores de linha por equipe.
-   Quantidade de goleiros por equipe.
-   Prazo limite para confirmação.
-   Prazo para cancelamento sem penalidade, se aplicável.
-   Janela de avaliação pós-jogo: 6 ou 24 horas.
-   Administradores responsáveis.

### 3.2. Recorrência

O administrador poderá configurar:

-   Partida avulsa.
-   Recorrência semanal.
-   Recorrência quinzenal.
-   Recorrência mensal.

Cada ocorrência recorrente deverá possuir registros próprios de
participantes, confirmações, presença, times, pagamentos e avaliações.
Alterações na configuração padrão não deverão modificar retroativamente
partidas já realizadas.

### 3.3. Gestão de jogadores

O administrador poderá:

-   Adicionar jogadores manualmente.
-   Convidar por link.
-   Aprovar solicitações de entrada, se habilitadas.
-   Remover ou inativar jogadores.
-   Consultar perfil esportivo e situação financeira.
-   Definir administradores auxiliares.

------------------------------------------------------------------------

## 4. Confirmação de presença

Cada ocorrência terá uma lista de participantes com os seguintes
estados:

  Status            Descrição
  ----------------- ------------------------------------
  Pendente          Ainda não respondeu
  Confirmado        Informou que irá jogar
  Recusado          Informou que não poderá comparecer
  Lista de espera   Deseja participar, mas não há vaga
  Cancelado         Desistiu após confirmar
  Presente          Compareceu à partida
  Ausente           Não compareceu

### 4.1. Fluxo

1.  O administrador cria ou publica uma ocorrência.
2.  Os jogadores recebem uma notificação.
3.  O jogador confirma ou recusa.
4.  O sistema atualiza as vagas.
5.  Quando as vagas acabarem, novos interessados entram na lista de
    espera.
6.  Se uma vaga for liberada, o próximo jogador da fila será notificado.
7.  O administrador registra a presença após a partida.

### 4.2. Regras

-   A ordem de confirmação será registrada com data e hora.
-   O limite de vagas será respeitado.
-   O administrador poderá realizar ajustes manuais.
-   Toda alteração manual deverá ser auditada.
-   O sistema deverá diferenciar confirmação de presença efetiva.
-   A lista de espera deverá respeitar uma ordem definida,
    preferencialmente a ordem de entrada.
-   O administrador poderá configurar se a promoção da lista de espera
    será automática ou dependerá de aceite do jogador.

------------------------------------------------------------------------

## 5. Perfil esportivo do jogador

Cada jogador terá uma ficha técnica com cinco atributos avaliados de 0 a
5 estrelas.

### 5.1. Atributos

  -----------------------------------------------------------------------
  Atributo                            Descrição
  ----------------------------------- -----------------------------------
  Finalização                         Capacidade de concluir jogadas e
                                      finalizar em gol

  Passe                               Precisão, visão de jogo e
                                      distribuição da bola

  Drible                              Habilidade individual, condução e
                                      capacidade de superar adversários

  Defesa                              Marcação, desarme, cobertura e
                                      posicionamento defensivo

  Físico                              Resistência, velocidade, força e
                                      intensidade
  -----------------------------------------------------------------------

A média geral será calculada a partir dos cinco atributos.

### 5.2. Cálculo da média

``` text
media = (finalizacao + passe + drible + defesa + fisico) / 5
```

A média será apresentada com uma casa decimal. O cálculo interno deverá
preservar precisão suficiente para evitar erros de classificação por
arredondamento.

### 5.3. Classificação por Tier

Proposta inicial:

  Tier           Média Classificação
  ------ ------------- -----------------
  S        4,50 a 5,00 Elite
  A        3,75 a 4,49 Avançado
  B        3,00 a 3,74 Intermediário
  C        2,00 a 2,99 Iniciante
  D        0,00 a 1,99 Desenvolvimento

O Tier será calculado automaticamente. Não deverá ser editável
manualmente pelo jogador. O administrador poderá consultar e auditar os
dados que originaram a classificação.

### 5.4. Jogadores novos e notas provisórias

Jogadores recém-cadastrados poderão ainda não ter avaliações
suficientes. O sistema deverá permitir uma nota inicial provisória,
definida conforme a regra escolhida pelo grupo.

A interface deverá identificar claramente perfis provisórios, evitando
tratar uma estimativa inicial como avaliação consolidada.

### 5.5. Evolução

O perfil deverá manter:

-   Nota atual de cada atributo.
-   Média geral.
-   Tier atual.
-   Quantidade de avaliações válidas.
-   Quantidade de partidas avaliadas.
-   Histórico de evolução por atributo.
-   Data da última atualização.

As notas não deverão ser sobrescritas sem histórico. Cada atualização
deverá gerar um registro que permita reconstruir a evolução do jogador.

------------------------------------------------------------------------

## 6. Habilidade especial do jogador

Além dos atributos, cada jogador poderá selecionar uma habilidade
principal que represente seu diferencial.

### 6.1. Exemplos

  Habilidade          Descrição
  ------------------- ------------------------------------------------
  Muralha             Sou muito bom na defesa
  Foguete             Sou muito rápido
  Matador             Tenho ótima finalização
  Driblador           Gosto de partir para cima
  Maestro             Tenho visão de jogo e bons passes
  Motorzinho          Tenho muita resistência e corro o jogo inteiro
  Jogo aéreo          Sou bom em bolas aéreas
  Forte fisicamente   Tenho força e proteção de bola
  Posicionamento      Sei ocupar espaços e me posicionar
  Bola parada         Sou especialista em faltas e escanteios
  Versátil            Jogo bem em diferentes posições
  Líder               Organizo e oriento o time

### 6.2. Regras

-   Cada jogador terá uma habilidade principal no MVP.
-   O jogador poderá escolher entre opções cadastradas.
-   O administrador poderá moderar alterações, conforme configuração do
    grupo.
-   A habilidade não alterará diretamente a média ou o Tier.
-   O algoritmo poderá usar a habilidade como critério complementar de
    equilíbrio.
-   A lista deverá ser configurável para permitir novas habilidades no
    futuro.

------------------------------------------------------------------------

## 7. Avaliação pós-pelada

Após cada partida, os participantes poderão avaliar os demais jogadores
que estiveram presentes.

### 7.1. Janela de avaliação

O administrador poderá configurar o prazo:

-   6 horas após o término previsto da partida.
-   24 horas após o término previsto da partida.

O prazo deverá ser calculado a partir do horário de término registrado
para a ocorrência. O sistema deverá armazenar o início e o encerramento
da janela.

### 7.2. Fluxo de avaliação

1.  A partida é encerrada.
2.  O administrador confirma quem esteve presente.
3.  O sistema abre a janela de avaliação.
4.  Os participantes recebem uma notificação.
5.  Cada jogador avalia os demais participantes elegíveis.
6.  O sistema valida e registra as avaliações.
7.  A janela é encerrada automaticamente.
8.  As notas consolidadas são atualizadas.

### 7.3. Formulário de avaliação

Cada avaliação conterá notas de 0 a 5 para:

-   Finalização.
-   Passe.
-   Drible.
-   Defesa.
-   Físico.

O avaliador não poderá avaliar a si próprio.

### 7.4. Regras contra manipulação

-   Apenas jogadores com presença registrada poderão avaliar.
-   Cada avaliador poderá avaliar cada participante uma única vez por
    ocorrência.
-   Não será permitido avaliar a si próprio.
-   A avaliação individual não deverá exibir publicamente o nome do
    avaliador.
-   O sistema deverá identificar notas muito discrepantes em relação ao
    conjunto.
-   Avaliações suspeitas poderão ser sinalizadas para análise.
-   O administrador poderá consultar indicadores de conclusão, sem poder
    alterar avaliações individuais sem registro de auditoria.
-   Avaliações não realizadas não deverão gerar notas fictícias.

### 7.5. Atualização das notas

Fórmula inicial sugerida:

``` text
nota_nova = (nota_anterior * 0,70) + (nota_da_partida * 0,30)
```

A nota da partida será consolidada a partir das avaliações válidas
recebidas naquele evento.

Os pesos são parâmetros iniciais e deverão ser calibrados após uso real.
O sistema deverá considerar também a quantidade e a confiabilidade das
avaliações para evitar grandes oscilações com poucas respostas.

### 7.6. Privacidade e transparência

O jogador deverá saber como suas notas são calculadas, quantas
avaliações foram consideradas e quando ocorreu a última atualização. A
identidade dos avaliadores não será exibida aos demais jogadores.

------------------------------------------------------------------------

## 8. Controle financeiro

O módulo financeiro será inicialmente administrativo, sem processamento
de pagamentos dentro do aplicativo.

### 8.1. Estados de pagamento

  Status      Descrição
  ----------- ----------------------------------
  Pendente    Valor ainda não recebido
  Pago        Pagamento confirmado
  Isento      Jogador dispensado do pagamento
  Estornado   Pagamento devolvido ou cancelado

### 8.2. Controle por ocorrência

Cada participação deverá possuir um lançamento financeiro vinculado à
ocorrência correspondente.

O sistema deverá registrar:

-   Jogador.
-   Partida.
-   Valor devido.
-   Valor pago.
-   Status.
-   Data de vencimento, se aplicável.
-   Data do pagamento.
-   Forma de pagamento informada.
-   Administrador que confirmou o recebimento.
-   Observações.

### 8.3. Pendência da última pelada

Se o jogador participou de uma partida e não pagou, o sistema deverá
manter a pendência vinculada àquela ocorrência.

Ao tentar confirmar uma nova partida, o jogador verá um aviso com:

-   Valor pendente.
-   Data da partida de origem.
-   Descrição da ocorrência.
-   Situação do pagamento.

### 8.4. Política de inadimplência

A existência de dívida não deverá bloquear automaticamente a confirmação
no MVP.

O sistema deverá:

-   Sinalizar a pendência para o jogador.
-   Sinalizar a pendência para o administrador.
-   Permitir que o administrador decida se libera ou não a participação.
-   Permitir quitação de uma ou várias pendências.
-   Preservar histórico de pagamentos e alterações.

### 8.5. Relatórios financeiros

O administrador poderá consultar:

-   Total previsto.
-   Total recebido.
-   Total pendente.
-   Jogadores inadimplentes.
-   Histórico por ocorrência.
-   Histórico por jogador.

Integração com Pix e gateways de pagamento ficará para uma versão
futura.

------------------------------------------------------------------------

## 9. Sorteio inteligente de times

O sorteio inteligente será um dos principais diferenciais do produto.

O objetivo é criar equipes equilibradas considerando a força geral dos
jogadores, os Tiers, os atributos individuais e as habilidades
especiais.

O sorteio deverá manter aleatoriedade entre composições de qualidade
semelhante, evitando que o resultado seja sempre determinístico.

### 9.1. Configuração

O administrador poderá definir:

-   Quantidade de equipes.
-   Quantidade de jogadores de linha por equipe.
-   Quantidade de goleiros por equipe.
-   Jogadores elegíveis para o sorteio.
-   Jogadores indisponíveis.
-   Restrições específicas, se habilitadas em versões futuras.

Exemplos de formação:

-   4 jogadores de linha + 1 goleiro.
-   5 jogadores de linha + 1 goleiro.
-   6 jogadores de linha + 1 goleiro.

### 9.2. Distribuição por Tier

A regra principal será distribuir os jogadores de maior nível entre as
equipes.

Exemplo com dois times:

  Time A        Time B
  ------------- -------------
  1 jogador S   1 jogador S
  1 jogador A   1 jogador A
  1 jogador B   1 jogador B
  1 jogador C   1 jogador C

Essa distribuição é uma referência, não uma obrigação absoluta. Se a
quantidade de jogadores por Tier não permitir uma divisão idêntica, o
algoritmo deverá buscar a melhor distribuição proporcional possível.

Para três ou mais equipes, o sistema deverá minimizar diferenças de
força total e evitar concentração de jogadores de Tier alto.

### 9.3. Etapas do algoritmo

**Etapa 1 --- Elegibilidade**

-   Considerar somente jogadores confirmados e elegíveis.
-   Separar goleiros de jogadores de linha.
-   Identificar perfis sem avaliação suficiente.
-   Validar a quantidade de jogadores para a formação escolhida.

**Etapa 2 --- Agrupamento por Tier**

Separar os jogadores nos grupos S, A, B, C e D.

**Etapa 3 --- Distribuição dos jogadores de maior nível**

Distribuir jogadores S e A entre as equipes, buscando evitar
concentração.

**Etapa 4 --- Distribuição dos demais jogadores**

Distribuir B, C e D considerando a força acumulada de cada equipe.

**Etapa 5 --- Equilíbrio por atributos**

Considerar a composição de cada equipe em:

-   Finalização.
-   Passe.
-   Drible.
-   Defesa.
-   Físico.

**Etapa 6 --- Equilíbrio por habilidades**

Evitar, quando possível, que habilidades importantes fiquem concentradas
em uma equipe. Exemplos: todos os defensores ou todos os jogadores
rápidos no mesmo time.

**Etapa 7 --- Avaliação das composições**

Calcular a diferença de força entre equipes e selecionar uma composição
com bom equilíbrio.

### 9.4. Pesos iniciais do algoritmo

  Critério                         Peso
  ------------------------------ ------
  Equilíbrio da média geral         40%
  Distribuição dos Tiers            25%
  Equilíbrio dos atributos          20%
  Distribuição das habilidades      15%

Esses pesos deverão ser configuráveis no backend e calibrados com dados
reais.

### 9.5. Função objetivo

O algoritmo deverá minimizar uma função de custo que considere:

-   Diferença entre médias gerais das equipes.
-   Diferença na soma dos níveis ou valores de Tier.
-   Diferença por atributo.
-   Concentração de habilidades especiais.
-   Diferença na quantidade de jogadores.

Uma implementação inicial poderá gerar várias composições candidatas e
selecionar uma das melhores, aplicando aleatoriedade entre resultados
com pontuação semelhante.

O algoritmo deverá registrar a composição, os parâmetros utilizados e a
pontuação de equilíbrio para permitir auditoria e comparação.

### 9.6. Goleiros

Goleiros deverão ser tratados separadamente dos jogadores de linha para
não distorcer a média geral das equipes.

Perfil especializado sugerido:

-   Reflexo.
-   Posicionamento.
-   Saída do gol.
-   Reposição.
-   Um contra um.

No MVP, o grupo poderá escolher entre:

1.  Usar uma avaliação própria de goleiro.
2.  Usar os mesmos cinco atributos gerais, com classificação separada.

O algoritmo deverá distribuir os goleiros de acordo com sua força
relativa, sempre que houver goleiros suficientes para todas as equipes.

### 9.7. Novo sorteio e aprovação

O administrador poderá:

-   Executar o sorteio.
-   Visualizar as equipes.
-   Consultar médias e Tiers por equipe.
-   Consultar a diferença estimada de força.
-   Gerar uma nova composição.
-   Comparar com o sorteio anterior.
-   Ajustar manualmente jogadores, se permitido.
-   Aprovar e publicar os times.

Cada execução deverá ser registrada no histórico.

------------------------------------------------------------------------

## 10. Painel administrativo

O dashboard do administrador deverá apresentar:

-   Próxima pelada.
-   Total de jogadores cadastrados.
-   Confirmados.
-   Pendentes de confirmação.
-   Lista de espera.
-   Ausências recentes.
-   Jogadores com pendências financeiras.
-   Total arrecadado.
-   Avaliações aguardando conclusão.
-   Últimos sorteios realizados.

### 10.1. Gestão da ocorrência

Cada ocorrência deverá possuir as seguintes áreas:

1.  Visão geral.
2.  Jogadores.
3.  Confirmações.
4.  Presença.
5.  Times.
6.  Pagamentos.
7.  Avaliações.
8.  Histórico e auditoria.

------------------------------------------------------------------------

## 11. Perfil do jogador

O perfil individual deverá exibir:

-   Foto.
-   Nome.
-   Posição preferida.
-   Tier.
-   Média geral.
-   Cinco atributos técnicos.
-   Habilidade especial.
-   Quantidade de partidas.
-   Presenças e ausências.
-   Histórico de avaliações.
-   Evolução dos atributos.
-   Situação financeira no grupo.

### 11.1. Estatísticas futuras

Em versões posteriores, poderão ser incluídos:

-   Gols.
-   Assistências.
-   Vitórias.
-   Derrotas.
-   Empates.
-   Aproveitamento.
-   Artilharia.
-   Participações em partidas.

Essas estatísticas complementarão as avaliações técnicas, mas não
deverão substituir os atributos utilizados no sorteio.

------------------------------------------------------------------------

## 12. Notificações

O sistema deverá suportar notificações internas e, futuramente, push.

  Evento                              Destinatário
  ----------------------------------- ----------------------------
  Nova pelada criada                  Jogadores do grupo
  Prazo de confirmação próximo        Jogadores pendentes
  Vaga liberada                       Próximo da lista de espera
  Confirmação registrada              Jogador
  Sorteio realizado                   Participantes
  Pagamento pendente                  Jogador
  Avaliação disponível                Participantes presentes
  Avaliação próxima do encerramento   Quem ainda não avaliou
  Times publicados                    Participantes

A integração com WhatsApp poderá ser avaliada em uma versão futura.

------------------------------------------------------------------------

## 13. Telas principais

### 13.1. Início

-   Próxima pelada.
-   Estado da confirmação.
-   Aviso de pendências.
-   Resumo das últimas atividades.

### 13.2. Pelada

-   Informações da ocorrência.
-   Lista de participantes.
-   Confirmação.
-   Lista de espera.
-   Valor e prazo.

### 13.3. Jogadores

-   Lista de jogadores.
-   Busca e filtros.
-   Tier e média.
-   Habilidade especial.
-   Acesso ao perfil.

### 13.4. Perfil esportivo

-   Dados do jogador.
-   Atributos.
-   Média e Tier.
-   Habilidade especial.
-   Evolução.

### 13.5. Sorteio

-   Configuração da formação.
-   Jogadores elegíveis.
-   Resultado por equipe.
-   Média e distribuição de Tiers.
-   Comparação de equilíbrio.
-   Ações de repetir, ajustar e publicar.

### 13.6. Avaliação

-   Lista de jogadores avaliáveis.
-   Formulário com cinco atributos.
-   Progresso das avaliações.
-   Prazo restante.

### 13.7. Financeiro

-   Resumo de valores.
-   Lista de pagamentos.
-   Pendências.
-   Histórico.

------------------------------------------------------------------------

## 14. Requisitos funcionais

  ID     Requisito                                      Prioridade
  ------ ---------------------------------------------- ------------
  RF01   Cadastro e autenticação de usuários            Alta
  RF02   Criação e edição de grupos de pelada           Alta
  RF03   Cadastro e convite de jogadores                Alta
  RF04   Criação de ocorrências recorrentes e avulsas   Alta
  RF05   Confirmação e cancelamento de presença         Alta
  RF06   Lista de espera                                Alta
  RF07   Registro de presença efetiva                   Alta
  RF08   Perfil esportivo do jogador                    Alta
  RF09   Cadastro dos cinco atributos                   Alta
  RF10   Cálculo de média e Tier                        Alta
  RF11   Cadastro de habilidade especial                Alta
  RF12   Avaliação pós-pelada                           Alta
  RF13   Janela de avaliação de 6h ou 24h               Alta
  RF14   Consolidação e histórico de avaliações         Alta
  RF15   Controle manual de pagamentos                  Alta
  RF16   Identificação de pendências anteriores         Alta
  RF17   Sorteio inteligente de equipes                 Alta
  RF18   Distribuição separada de goleiros              Alta
  RF19   Repetição e aprovação do sorteio               Alta
  RF20   Dashboard administrativo                       Média
  RF21   Notificações                                   Média
  RF22   Histórico de desempenho                        Média
  RF23   Estatísticas de gols e assistências            Baixa

------------------------------------------------------------------------

## 15. Regras de negócio

  -----------------------------------------------------------------------
  ID                                  Regra
  ----------------------------------- -----------------------------------
  RN01                                Somente jogadores confirmados e com
                                      presença registrada poderão avaliar
                                      participantes.

  RN02                                O jogador não poderá avaliar a si
                                      próprio.

  RN03                                Cada avaliador poderá avaliar cada
                                      participante uma única vez por
                                      ocorrência.

  RN04                                A média será calculada a partir dos
                                      cinco atributos técnicos.

  RN05                                O Tier será calculado
                                      automaticamente com base na média.

  RN06                                As avaliações serão encerradas após
                                      6h ou 24h, conforme configuração.

  RN07                                Avaliações e alterações de nota
                                      deverão manter histórico.

  RN08                                Pagamentos deverão ser vinculados à
                                      ocorrência que originou a cobrança.

  RN09                                Pendências deverão continuar
                                      visíveis até serem quitadas ou
                                      formalmente estornadas.

  RN10                                A pendência financeira não
                                      bloqueará automaticamente a
                                      confirmação no MVP.

  RN11                                O administrador poderá decidir
                                      sobre a participação de jogadores
                                      inadimplentes.

  RN12                                O sorteio deverá considerar Tier,
                                      média, atributos e habilidades.

  RN13                                Jogadores S deverão ser
                                      distribuídos proporcionalmente
                                      entre equipes, quando possível.

  RN14                                Goleiros serão tratados
                                      separadamente dos jogadores de
                                      linha.

  RN15                                Toda alteração administrativa
                                      relevante deverá ser auditável.

  RN16                                Sorteios deverão manter histórico
                                      de composição e parâmetros
                                      utilizados.
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## 16. Escopo do MVP

O MVP deverá validar o fluxo completo de organização da pelada e,
principalmente, a qualidade do sorteio.

### Módulo 1 --- Organização

-   Cadastro e login.
-   Criação de grupos e peladas.
-   Cadastro e convite de jogadores.
-   Confirmação e cancelamento.
-   Lista de espera.
-   Registro de presença.

### Módulo 2 --- Jogadores

-   Perfil esportivo.
-   Cinco atributos.
-   Média e classificação por Tier.
-   Habilidade especial.

### Módulo 3 --- Avaliação

-   Avaliação pós-pelada.
-   Janela de 6h ou 24h.
-   Atualização das notas.
-   Histórico de avaliações.
-   Controle de avaliações pendentes.

### Módulo 4 --- Financeiro

-   Registro manual de pagamentos.
-   Controle de pendências.
-   Histórico por jogador e ocorrência.
-   Resumo financeiro.

### Módulo 5 --- Sorteio

-   Sorteio de equipes.
-   Distribuição por Tier.
-   Equilíbrio por média e atributos.
-   Consideração de habilidades.
-   Distribuição de goleiros.
-   Repetição e aprovação do sorteio.
-   Histórico das composições.

### 16.1. Fora do MVP

-   Pagamento via Pix integrado.
-   Integração com WhatsApp.
-   Estatísticas automáticas de gols e assistências.
-   Ranking público.
-   Premiações e conquistas.
-   Histórico de confrontos.
-   Sugestões táticas e posicionamento automático.
-   Inteligência artificial generativa para análise de partidas.

------------------------------------------------------------------------

## 17. Arquitetura técnica sugerida

A arquitetura deverá ser simples, modular e preparada para evolução.

  Camada                     Tecnologia
  -------------------------- -----------------------------
  Frontend                   Angular
  Backend                    Java + Spring Boot
  Persistência               PostgreSQL
  ORM                        Spring Data JPA / Hibernate
  Autenticação               Spring Security + JWT
  Infraestrutura             AWS
  Armazenamento de imagens   AWS S3
  Notificações push          Firebase Cloud Messaging
  Comunicação                API REST
  Documentação               OpenAPI / Swagger

### 17.1. Módulos de domínio do backend

``` text
com.peladamais
├── auth
├── user
├── player
├── group
├── match
│   ├── attendance
│   ├── presence
│   └── scheduling
├── evaluation
├── rating
├── financial
├── team
│   └── draw
├── notification
└── shared
```

### 17.2. Entidades principais

  Entidade              Responsabilidade
  --------------------- ----------------------------------------
  User                  Conta e autenticação
  Player                Perfil esportivo
  FootballGroup         Grupo de pelada
  GroupMember           Relação entre usuário e grupo
  Match                 Ocorrência da pelada
  MatchPlayer           Participação e status do jogador
  PlayerSkill           Atributos e habilidade especial
  Evaluation            Avaliação realizada por um jogador
  PlayerRatingHistory   Histórico de notas
  Payment               Cobrança e pagamento
  Team                  Equipe sorteada
  TeamPlayer            Jogadores de cada equipe
  Draw                  Execução e resultado do sorteio
  Notification          Notificações
  AuditLog              Registro de alterações administrativas

### 17.3. Diretrizes arquiteturais

-   Manter cálculo de média e Tier no backend.
-   Não confiar em valores calculados enviados pelo frontend.
-   Isolar o algoritmo de sorteio em um componente de domínio.
-   Manter histórico de avaliações e alterações de notas.
-   Garantir que cada grupo tenha seus próprios jogadores, regras e
    ocorrências.
-   Garantir isolamento de dados entre grupos.
-   Usar transações nas operações financeiras e na publicação de times.
-   Registrar eventos importantes para auditoria.
-   Documentar endpoints com OpenAPI.
-   Criar testes unitários para regras de negócio e testes de integração
    para fluxos críticos.

------------------------------------------------------------------------

## 18. Requisitos não funcionais

### Segurança

-   Autenticação segura.
-   Controle de acesso por grupo e papel.
-   Proteção contra acesso a dados de outros grupos.
-   Validação de entrada no backend.
-   Armazenamento seguro de credenciais.
-   Registro de operações administrativas.
-   Proteção de dados pessoais.

### Desempenho

-   Operações comuns deverão responder rapidamente em condições normais.
-   O sorteio deverá suportar a quantidade de jogadores configurada para
    o grupo sem bloquear a aplicação.
-   Consultas de histórico deverão utilizar paginação.
-   O cálculo do sorteio deverá possuir limite de tempo e estratégia de
    fallback.

### Disponibilidade e consistência

-   Pagamentos não poderão ser registrados em duplicidade por falha de
    requisição.
-   Uma avaliação não poderá ser duplicada para o mesmo avaliador,
    avaliado e ocorrência.
-   Um jogador não poderá ocupar duas equipes na mesma execução de
    sorteio.
-   A publicação dos times deverá ser atômica.
-   O encerramento de avaliações deverá ser idempotente.

### Privacidade

-   Exibir apenas os dados necessários para cada papel.
-   Não revelar a identidade de quem realizou uma avaliação individual.
-   Permitir correção de dados pessoais conforme as regras aplicáveis.
-   Considerar os princípios da LGPD no tratamento de dados.

------------------------------------------------------------------------

## 19. Critérios de aceite do MVP

O MVP será considerado funcional quando:

1.  Um administrador conseguir criar um grupo e uma pelada.
2.  Um jogador conseguir entrar por convite.
3.  Os jogadores conseguirem confirmar ou recusar presença.
4.  O sistema controlar vagas e lista de espera.
5.  O administrador conseguir registrar presença.
6.  O jogador possuir cinco atributos técnicos.
7.  O sistema calcular média e Tier corretamente.
8.  O jogador conseguir escolher uma habilidade especial.
9.  Os participantes presentes conseguirem avaliar os demais.
10. O sistema respeitar a janela de avaliação configurada.
11. As notas forem atualizadas e o histórico preservado.
12. O administrador conseguir registrar pagamentos.
13. O sistema identificar pendências anteriores.
14. O organizador conseguir executar um sorteio.
15. O sorteio distribuir jogadores de Tier alto proporcionalmente.
16. O sorteio buscar equilíbrio entre as médias das equipes.
17. O sorteio considerar atributos e habilidades.
18. Os goleiros forem tratados separadamente.
19. O administrador conseguir repetir, ajustar e publicar os times.
20. O histórico de sorteios e alterações ficar disponível.

------------------------------------------------------------------------

## 20. Decisões de produto pendentes

As decisões abaixo deverão ser confirmadas antes de fechar a
especificação técnica executável.

  -----------------------------------------------------------------------
  Tema                    Opções                  Recomendação inicial
  ----------------------- ----------------------- -----------------------
  Nota inicial            Comunidade avalia;      Nota provisória +
                          administrador define;   avaliações
                          nota provisória +       
                          avaliações              

  Goleiros                Mesmos atributos;       Atributos próprios
                          atributos próprios;     
                          dois perfis             

  Sorteio                 Equilíbrio primeiro;    Equilíbrio primeiro,
                          Tier rígido; aleatório  com aleatoriedade entre
                          com correção            resultados equivalentes

  Inadimplência           Aviso; bloqueio;        Decisão do
                          decisão do              administrador
                          administrador           

  Avaliação               Todos avaliam todos; só Todos avaliam todos
                          adversários; ambos      
                          separados               
  -----------------------------------------------------------------------

### 20.1. Decisões adicionais

Também deverão ser definidos:

-   Se o jogador poderá participar de vários grupos com perfis e notas
    independentes.
-   Se a nota será global ou específica por grupo.
-   Se o administrador poderá ajustar manualmente a composição depois do
    sorteio.
-   Se a habilidade especial poderá ser alterada a qualquer momento ou
    somente após aprovação.
-   Se haverá limite de avaliações mínimas para consolidar uma nota.
-   Como serão tratados jogadores sem avaliações suficientes.
-   Se a lista de espera terá promoção automática.
-   Se faltas após confirmação terão alguma penalidade.

------------------------------------------------------------------------

## 21. Métricas de sucesso

O produto deverá acompanhar indicadores que ajudem a validar sua
utilidade:

-   Percentual de jogadores que respondem às confirmações.
-   Taxa de ocupação das vagas.
-   Quantidade de desistências após confirmação.
-   Percentual de partidas com avaliações concluídas.
-   Tempo médio para concluir avaliações.
-   Percentual de pagamentos registrados.
-   Quantidade de pendências em aberto.
-   Diferença média de força entre equipes sorteadas.
-   Frequência de repetição manual dos sorteios.
-   Retenção de jogadores e grupos.

A métrica mais importante do módulo de sorteio será a diferença de força
entre equipes, acompanhada da satisfação dos participantes com o
equilíbrio das partidas.

------------------------------------------------------------------------

## 22. Visão de evolução do produto

### Versão 1.0 --- MVP

Organização de peladas, confirmação, perfis, avaliações, financeiro
manual e sorteio inteligente.

### Versão 1.1 --- Engajamento

Notificações push, estatísticas de participação, melhorias no histórico
e ranking privado do grupo.

### Versão 1.2 --- Financeiro

Integração com Pix, comprovantes e conciliação de pagamentos.

### Versão 2.0 --- Inteligência esportiva

Estatísticas de gols e assistências, histórico de confrontos,
recomendações de posições e algoritmo de equilíbrio aprimorado.

### Versão 3.0 --- Ecossistema

Descoberta de peladas, quadras, comunidades, convites públicos e
funcionalidades para estabelecimentos esportivos.

------------------------------------------------------------------------

## 23. Resumo executivo

O Pelada+ será uma plataforma de gestão de futebol amador que conecta
organização, comunidade e desempenho esportivo.

Seu diferencial central será o perfil técnico evolutivo de cada jogador,
alimentado por avaliações pós-partida e utilizado por um algoritmo
inteligente para montar equipes equilibradas.

O MVP deverá priorizar cinco pilares:

1.  **Organização:** grupos, partidas e confirmações.
2.  **Jogadores:** perfis, atributos, média, Tier e habilidades.
3.  **Avaliação:** feedback pós-jogo com janela de 6h ou 24h.
4.  **Financeiro:** pagamentos e pendências por partida.
5.  **Sorteio inteligente:** distribuição equilibrada de jogadores de
    linha e goleiros.

A prioridade de desenvolvimento deverá ser entregar um fluxo completo e
confiável: criar uma pelada, confirmar participantes, registrar
presença, avaliar jogadores, atualizar seus níveis, controlar pagamentos
e sortear times equilibrados.
