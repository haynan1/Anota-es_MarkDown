"""A trava que impede um mapa mental de mudar sem querer.

Não é o cadeado do documento, e a diferença é o ponto. Um documento travado
continua sendo editado e só resiste à exclusão; um mapa travado fica somente
leitura por inteiro, porque o acidente que ele existe para impedir é uma tecla
numa tela - Delete com um ramo selecionado, um arrastar distraído que reaninha
metade do mapa - e não um clique numa lixeira.

O que este arquivo prende:

* toda escrita passa por ``ensure_unlocked``, inclusive as que ainda não
  existem: o teste percorre a superfície pública do serviço e falha se um
  método novo escrever sem a porta;
* a recusa é ``423`` e chega à tela como JSON, para o quadro saber que não foi
  culpa do que ele mandou;
* uma recusa não gasta revisão - a outra aba não pode ser invalidada por um
  lote que nem foi aplicado;
* ler, exportar, duplicar e enquadrar continuam, porque nada disso altera o
  mapa;
* a página desabilita o que escreve, e não apenas o apaga.
"""

from __future__ import annotations

import uuid as uuid_module

import pytest

from app.models import MindMap
from app.repositories.mind_map_repository import MindMapRepository
from app.services.exceptions import LockedError
from app.services.mind_map_service import MindMapService


@pytest.fixture()
def mind_map(app):
    return MindMapService.create("Plano trimestral", "O que precisa acontecer")


@pytest.fixture()
def root(mind_map):
    return MindMapRepository.nodes_of(mind_map)[0]


@pytest.fixture()
def locked(mind_map):
    MindMapService.toggle_lock(mind_map)
    return mind_map


def create_op(parent=None, **fields):
    return {
        "type": "node.create",
        "uuid": str(uuid_module.uuid4()),
        "parent": parent,
        "fields": fields,
    }


# ── O interruptor ───────────────────────────────────────────────────────────


class TestTheSwitch:
    def test_a_map_is_born_unlocked(self, app, mind_map):
        assert mind_map.is_locked is False

    def test_it_toggles_both_ways(self, app, mind_map):
        assert MindMapService.toggle_lock(mind_map) is True
        assert MindMapService.toggle_lock(mind_map) is False

    def test_it_survives_a_reload(self, app, db, locked):
        identifier = locked.id
        db.session.expire_all()
        assert db.session.get(MindMap, identifier).is_locked is True

    def test_the_board_is_told(self, app, locked):
        """A tela precisa saber antes de desenhar o primeiro quadro."""
        assert MindMapService.graph_payload(locked)["locked"] is True


# ── O que a trava recusa ────────────────────────────────────────────────────


class TestEveryWriteIsRefused:
    def test_operations_are_refused(self, app, locked, root):
        with pytest.raises(LockedError):
            MindMapService.apply_operations(locked, [create_op(parent=root.uuid)])

        assert len(MindMapRepository.nodes_of(locked)) == 1

    def test_an_empty_batch_is_refused_too(self, app, locked):
        """Um lote vazio ainda é uma escrita: ele reivindica a revisão."""
        with pytest.raises(LockedError):
            MindMapService.apply_operations(locked, [])

    def test_renaming_is_refused(self, app, locked):
        with pytest.raises(LockedError):
            MindMapService.update(locked, title="Outro nome")

        assert locked.title == "Plano trimestral"

    def test_tidying_is_refused(self, app, locked):
        with pytest.raises(LockedError):
            MindMapService.autolayout(locked, "radial")

        assert locked.layout == "right"

    def test_the_trash_is_refused(self, app, locked):
        with pytest.raises(LockedError):
            MindMapService.soft_delete(locked)

        assert locked.is_deleted is False

    def test_purging_is_refused(self, app, mind_map):
        """Travado depois de ir para a lixeira, continua travado dentro dela."""
        MindMapService.soft_delete(mind_map)
        MindMapService.toggle_lock(mind_map)

        with pytest.raises(LockedError):
            MindMapService.purge(mind_map)

        assert MindMapService.require(mind_map.uuid, include_deleted=True) is not None

    def test_the_message_says_which_map_and_what_to_do(self, app, locked):
        with pytest.raises(LockedError) as excinfo:
            MindMapService.update(locked, title="x")

        assert "Plano trimestral" in excinfo.value.message
        assert "Destrave" in excinfo.value.message


class TestARefusalCostsNothing:
    def test_the_revision_does_not_move(self, app, locked, root):
        """A recusa vem antes da reivindicação da revisão.

        Se viesse depois, um mapa travado invalidaria o lote que outra aba
        estava compondo - uma trava que atrapalha quem não pediu nada.
        """
        before = locked.revision

        with pytest.raises(LockedError):
            MindMapService.apply_operations(
                locked, [create_op(parent=root.uuid)], expected_revision=before
            )

        assert locked.revision == before

    def test_the_map_is_untouched_after_a_refused_batch(self, app, locked, root):
        with pytest.raises(LockedError):
            MindMapService.apply_operations(
                locked,
                [
                    create_op(parent=root.uuid, text="Um"),
                    create_op(parent=root.uuid, text="Dois"),
                ],
            )

        assert [node.text for node in MindMapRepository.nodes_of(locked)] == [
            "Plano trimestral"
        ]


# ── O que a trava não recusa ────────────────────────────────────────────────


class TestReadingStaysOpen:
    def test_the_canvas_still_opens(self, app, client, locked):
        response = client.get(f"/mapas/{locked.uuid}")
        assert response.status_code == 200

    def test_the_viewport_is_still_remembered(self, app, locked):
        """Panorâmica não é edição, e travar um mapa não é congelar a câmera."""
        MindMapService.save_viewport(locked, x=120, y=-40, zoom=1.5)
        assert locked.viewport_x == 120

    def test_favouriting_still_works(self, app, locked):
        assert MindMapService.toggle_favorite(locked) is True

    def test_it_can_still_be_duplicated_and_the_copy_is_open(self, app, locked):
        clone = MindMapService.duplicate(locked)
        assert clone.is_locked is False
        assert len(MindMapRepository.nodes_of(clone)) == 1

    def test_every_export_still_works(self, app, locked):
        assert "Plano trimestral" in MindMapService.export_markdown(locked)
        assert "<svg" in MindMapService.export_svg(locked)
        for fmt in ("pdf", "png", "jpeg"):
            assert MindMapService.export_picture(locked, fmt).data

    def test_it_can_still_become_a_document(self, app, locked):
        document = MindMapService.to_document(locked)
        assert document.title


# ── A superfície inteira ────────────────────────────────────────────────────


class TestNoWriteEscapesTheDoor:
    """A porta tem de valer para o método que alguém acrescentar amanhã.

    Percorrer a superfície pública e exigir que toda escrita recuse é o que
    transforma "lembramos de travar todas" numa afirmação verificada em vez de
    uma promessa. Um método novo que escreva sem passar por
    ``ensure_unlocked`` faz este teste falhar com o nome dele.
    """

    #: O que um mapa travado ainda faz, e por quê. Cada linha é uma decisão,
    #: não uma isenção: se algum dia uma delas passar a escrever, ela sai
    #: daqui e o teste acima passa a cobri-la.
    ALLOWED = {
        "require": "leitura",
        "create": "cria outro mapa, não altera este",
        "toggle_favorite": "não é o mapa, é a lista",
        "toggle_lock": "é a saída",
        "ensure_unlocked": "é a porta",
        "restore": "tira da lixeira, não altera o conteúdo",
        "duplicate": "escreve num mapa novo",
        "graph_payload": "leitura",
        "save_viewport": "a câmera, não o mapa",
        "frame": "leitura",
        "from_outline": "escreve num mapa novo",
        "from_document": "escreve num mapa novo",
        "to_document": "escreve num documento",
        "export_markdown": "leitura",
        "export_svg": "leitura",
        "export_picture": "leitura",
        "PICTURE_FORMATS": "uma tabela, não um método",
    }

    WRITES = {
        "update": lambda mind_map: MindMapService.update(mind_map, title="x"),
        "soft_delete": MindMapService.soft_delete,
        "purge": MindMapService.purge,
        "autolayout": MindMapService.autolayout,
        "apply_operations": lambda mind_map: MindMapService.apply_operations(
            mind_map, [create_op()]
        ),
    }

    def test_the_inventory_covers_the_whole_service(self):
        public = {
            name
            for name in vars(MindMapService)
            if not name.startswith("_")
        }
        unaccounted = public - set(self.ALLOWED) - set(self.WRITES)
        assert not unaccounted, (
            "Método novo em MindMapService sem decisão sobre o cadeado: "
            f"{sorted(unaccounted)}. Se ele escreve, chame ensure_unlocked e "
            "liste-o em WRITES; se não, liste-o em ALLOWED com o motivo."
        )

    @pytest.mark.parametrize("name", sorted(WRITES))
    def test_each_writing_method_refuses(self, app, locked, name):
        with pytest.raises(LockedError):
            self.WRITES[name](locked)


# ── Pela web ────────────────────────────────────────────────────────────────


class TestOverHttp:
    def test_the_api_answers_423_as_json(self, app, client, locked, root):
        response = client.post(
            f"/api/mapas/{locked.uuid}/operacoes",
            json={"revision": locked.revision, "operations": [create_op(parent=root.uuid)]},
        )
        assert response.status_code == 423
        assert response.get_json()["ok"] is False

    def test_tidying_over_http_is_refused(self, app, client, locked):
        response = client.post(f"/api/mapas/{locked.uuid}/organizar", json={"layout": "radial"})
        assert response.status_code == 423

    def test_the_viewport_endpoint_still_answers(self, app, client, locked):
        response = client.post(
            f"/api/mapas/{locked.uuid}/enquadramento", json={"x": 1, "y": 2, "zoom": 1}
        )
        assert response.status_code == 200

    def test_the_form_toggles_it(self, app, client, mind_map):
        response = client.post(f"/mapas/{mind_map.uuid}/travar", follow_redirects=True)
        assert response.status_code == 200
        assert mind_map.is_locked is True

    def test_deleting_over_http_is_refused(self, app, client, locked):
        response = client.post(f"/mapas/{locked.uuid}/excluir")
        assert response.status_code == 423
        assert locked.is_deleted is False


class TestTheSwitchIsAsProtectedAsEveryOtherButton:
    """Travar e destravar é um POST de formulário, e paga o mesmo pedágio.

    Sem isto, uma página hostil poderia destravar o mapa de quem a visitasse -
    e, tendo destravado, tudo o que o cadeado protegia passaria a estar ao
    alcance da requisição seguinte.
    """

    def test_toggling_without_a_token_is_refused(self, csrf_app):
        """Recusado por ``CSRFProtect``, antes da rota - daí o 400 e não um
        redirecionamento. O que importa é a segunda linha: o cadeado não se
        moveu."""
        with csrf_app.test_client() as client:
            protegido = MindMapService.create("Protegido")
            response = client.post(f"/mapas/{protegido.uuid}/travar")

            assert response.status_code == 400
            assert protegido.is_locked is False

    def test_a_hostile_next_never_leaves_this_application(self, app, client, mind_map):
        response = client.post(
            f"/mapas/{mind_map.uuid}/travar", data={"next": "https://exemplo.mau/"}
        )

        assert response.status_code == 302
        assert not response.headers["Location"].startswith("https://exemplo.mau")


class TestThePageSaysSo:
    def test_the_board_is_marked_and_its_writing_tools_disabled(self, app, client, locked):
        html = client.get(f"/mapas/{locked.uuid}").get_data(as_text=True)

        assert "data-locked" in html
        assert "Somente leitura" in html
        # Desabilitados de verdade, e não só apagados: um botão que o leitor de
        # tela ainda anuncia como disponível é pior do que um botão ausente.
        assert 'data-action="mm-add-topic" disabled' in html
        assert 'data-action="mm-organize" disabled' in html
        assert 'data-inspector-form hidden\n                  disabled' in html

    def test_an_open_map_keeps_its_tools(self, app, client, mind_map):
        html = client.get(f"/mapas/{mind_map.uuid}").get_data(as_text=True)

        assert "data-locked" not in html
        assert 'data-action="mm-add-topic" disabled' not in html
        assert "data-save-status" in html

    def test_the_trash_button_is_not_offered_on_a_locked_map(self, app, client, locked):
        html = client.get(f"/mapas/{locked.uuid}").get_data(as_text=True)
        assert f"/mapas/{locked.uuid}/excluir" not in html

    def test_the_gallery_shows_the_padlock(self, app, client, locked):
        html = client.get("/mapas/").get_data(as_text=True)
        assert "Travado contra alterações" in html
        assert f"/mapas/{locked.uuid}/travar" in html
