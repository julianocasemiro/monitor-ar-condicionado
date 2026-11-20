/**
 * Codigo.gs — Webhook que cria um evento no SEU Google Agenda.
 *
 * Por que Apps Script e não uma "conta de serviço" do Google Cloud?
 * Os lembretes (notificações) de um evento pertencem a quem está autenticado.
 * Um script publicado como "Executar como: Eu" cria o evento COMO VOCÊ,
 * então o lembrete toca no SEU celular. (Detalhes em docs/DECISOES.md, ADR-004.)
 *
 * Segurança: a URL do Web App fica acessível a "Qualquer pessoa". Por isso o script
 * só aceita pedidos que tragam o token secreto guardado nas Propriedades do script.
 * Quem não tiver o token recebe "nao autorizado" e nada é criado.
 */

// Quantos minutos no futuro o evento começa, e quando o lembrete toca.
var MINUTOS_ATE_O_EVENTO = 10;
var LEMBRETES_MINUTOS_ANTES = [9, 0];   // toca ~1 min depois de criado e de novo no início
var DURACAO_MINUTOS = 30;

function doPost(e) {
  try {
    var dados = JSON.parse(e.postData.contents);
    var tokenCorreto = PropertiesService.getScriptProperties().getProperty('TOKEN');

    if (!tokenCorreto || dados.token !== tokenCorreto) {
      return responder({ ok: false, erro: 'nao autorizado' });
    }

    var inicio = new Date(Date.now() + MINUTOS_ATE_O_EVENTO * 60 * 1000);
    var fim = new Date(inicio.getTime() + DURACAO_MINUTOS * 60 * 1000);
    var titulo = String(dados.titulo || 'Oferta de ar-condicionado').slice(0, 200);
    var descricao = String(dados.descricao || '').slice(0, 4000);

    var evento = CalendarApp.getDefaultCalendar().createEvent(titulo, inicio, fim, {
      description: descricao,
      location: String(dados.link || '')
    });
    evento.removeAllReminders();
    LEMBRETES_MINUTOS_ANTES.forEach(function (m) { evento.addPopupReminder(m); });
    evento.setColor(CalendarApp.EventColor.RED);

    return responder({ ok: true, id: evento.getId() });
  } catch (erro) {
    return responder({ ok: false, erro: String(erro) });
  }
}

function responder(objeto) {
  return ContentService.createTextOutput(JSON.stringify(objeto))
    .setMimeType(ContentService.MimeType.JSON);
}

/**
 * Rode esta função UMA vez pelo editor (botão "Executar") para testar a permissão
 * da agenda. Ela cria um evento de teste daqui a 10 minutos.
 */
function testarManualmente() {
  var token = PropertiesService.getScriptProperties().getProperty('TOKEN');
  var resposta = doPost({ postData: { contents: JSON.stringify({
    token: token,
    titulo: 'TESTE do monitor de ar-condicionado',
    descricao: 'Se você está vendo isto, a integração com a agenda funciona.',
    link: 'https://example.com'
  }) } });
  Logger.log(resposta.getContent());
}
