"""What happens to uploaded files when their document goes away.

Found in the engineering pass: `delete_asset` existed but nothing called it.
The media foreign key is ON DELETE SET NULL, so purging a document orphaned
the row and left the file on disk permanently — storage that only grows,
holding content the user believes they deleted.
"""

from __future__ import annotations

import base64
import io

from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import MediaAsset
from app.services.document_service import DocumentService
from app.services.media_service import asset_path, prune_orphans, store_upload
from app.utils.dates import utcnow

REAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
    "AAAABJRU5ErkJggg=="
)


def upload(name="foto.png"):
    return FileStorage(stream=io.BytesIO(REAL_PNG), filename=name)


class TestPurgeRemovesMedia:
    def test_purging_a_document_deletes_its_files(self, app, db, make_document):
        document = make_document(title="Com anexo")
        asset = store_upload(upload(), document_id=document.id)
        path = asset_path(asset)
        assert path.is_file()

        DocumentService.move_to_trash(document)
        DocumentService.purge(document)

        assert not path.exists(), "o arquivo sobreviveu ao documento"
        assert db.session.get(MediaAsset, asset.id) is None

    def test_emptying_the_trash_deletes_media_too(self, app, db, make_document):
        paths = []
        for index in range(3):
            document = make_document(title=f"Anexo {index}")
            asset = store_upload(upload(), document_id=document.id)
            paths.append(asset_path(asset))
            DocumentService.move_to_trash(document)

        DocumentService.empty_trash()

        assert not any(p.exists() for p in paths)
        assert db.session.scalar(db.select(db.func.count(MediaAsset.id))) == 0

    def test_a_locked_document_keeps_its_media(self, app, db, make_document):
        """The lock stops the purge, so nothing may be deleted underneath it.

        Locked after trashing: locking first makes `move_to_trash` refuse
        outright, which is a different guarantee (covered in test_lock).
        """
        document = make_document(title="Protegido")
        asset = store_upload(upload(), document_id=document.id)
        path = asset_path(asset)

        DocumentService.move_to_trash(document)
        DocumentService.toggle_lock(document)

        removed = DocumentService.empty_trash()

        assert removed == 0
        assert path.is_file()
        assert db.session.get(MediaAsset, asset.id) is not None

    def test_media_of_other_documents_is_untouched(self, app, db, make_document):
        keeper = make_document(title="Fica")
        kept = store_upload(upload(), document_id=keeper.id)
        kept_path = asset_path(kept)

        doomed = make_document(title="Sai")
        store_upload(upload(), document_id=doomed.id)

        DocumentService.move_to_trash(doomed)
        DocumentService.purge(doomed)

        assert kept_path.is_file()
        assert db.session.get(MediaAsset, kept.id) is not None

    def test_a_missing_file_does_not_block_the_purge(self, app, db, make_document):
        """A row without a file must still be deletable."""
        document = make_document(title="Arquivo sumiu")
        asset = store_upload(upload(), document_id=document.id)
        asset_path(asset).unlink()

        DocumentService.move_to_trash(document)
        DocumentService.purge(document)

        assert db.session.get(MediaAsset, asset.id) is None


class TestPruneOrphans:
    def test_an_unreferenced_upload_is_pruned(self, app, db):
        asset = store_upload(upload())
        path = asset_path(asset)
        # Age it past the guard.
        asset.created_at = utcnow().replace(year=utcnow().year - 1)
        db.session.commit()

        rows, files = prune_orphans()

        assert rows == 1
        assert files == 1
        assert not path.exists()

    def test_a_referenced_upload_survives(self, app, db, make_document):
        asset = store_upload(upload())
        asset.created_at = utcnow().replace(year=utcnow().year - 1)
        db.session.commit()

        make_document(title="Usa a imagem", content=f"![x](/midia/{asset.uuid})")

        rows, _ = prune_orphans()

        assert rows == 0
        assert asset_path(asset).is_file()

    def test_media_of_a_trashed_document_survives(self, app, db, make_document):
        """The trash is reversible — restoring must not yield broken images."""
        asset = store_upload(upload())
        asset.created_at = utcnow().replace(year=utcnow().year - 1)
        db.session.commit()

        document = make_document(title="Na lixeira", content=f"![x](/midia/{asset.uuid})")
        DocumentService.move_to_trash(document)

        rows, _ = prune_orphans()

        assert rows == 0
        assert asset_path(asset).is_file()

    def test_a_fresh_upload_is_left_alone(self, app, db):
        """An unsaved editor tab must not have its upload swept away."""
        asset = store_upload(upload())

        rows, _ = prune_orphans(max_age_hours=24)

        assert rows == 0
        assert asset_path(asset).is_file()

    def test_pruning_an_empty_library_is_a_no_op(self, app):
        assert prune_orphans() == (0, 0)

    def test_prune_does_not_scale_queries_with_assets(self, app, db):
        """One pass over the corpus, not a LIKE per asset."""
        from tests.test_performance import QueryCounter

        for _ in range(6):
            asset = store_upload(upload())
            asset.created_at = utcnow().replace(year=utcnow().year - 1)
        db.session.commit()

        with QueryCounter() as counter:
            prune_orphans()

        assert counter.count <= 12, f"{counter.count} consultas para 6 mídias"
