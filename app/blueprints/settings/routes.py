from __future__ import annotations

from flask import current_app, flash, redirect, render_template, request, send_file, url_for

from app.blueprints.documents.forms import ConfirmForm
from app.blueprints.settings import settings_bp
from app.blueprints.settings.forms import BackupRestoreForm, SettingsForm
from app.services.backup_service import (
    create_backup,
    list_backups,
    resolve_backup,
    restore_backup,
)
from app.services.exceptions import ServiceError
from app.services.media_service import enforce_content_length
from app.services.pdf_service import engine_info
from app.services.search_service import search_index
from app.services.settings_service import SettingsService


@settings_bp.route("/", methods=["GET", "POST"])
def index():
    current = SettingsService.all()
    form = SettingsForm(data=current if request.method == "GET" else None)

    if form.validate_on_submit():
        SettingsService.update_many(form.to_settings())
        flash("Configurações salvas.", "success")
        return redirect(url_for("settings.index"))
    if form.is_submitted() and form.errors:
        flash("Verifique os campos destacados.", "error")

    return render_template(
        "settings/index.html",
        form=form,
        backups=list_backups(),
        restore_form=BackupRestoreForm(),
        confirm_form=ConfirmForm(),
        pdf_engine=engine_info(),
        search_available=search_index.available,
        title_search_available=search_index.titles_available,
    )


@settings_bp.post("/backups/criar")
def create_backup_route():
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(url_for("settings.index"))

    try:
        info = create_backup()
    except ServiceError as error:
        flash(error.message, "error")
        return redirect(url_for("settings.index"))

    flash(f"Backup criado: {info.name} ({info.size_readable}).", "success")
    return redirect(url_for("settings.index"))


@settings_bp.get("/backups/<path:name>/baixar")
def download_backup(name: str):
    try:
        path = resolve_backup(name)
    except ServiceError as error:
        flash(error.message, "error")
        return redirect(url_for("settings.index"))

    return send_file(
        path, mimetype="application/zip", as_attachment=True, download_name=path.name
    )


@settings_bp.post("/backups/<path:name>/restaurar")
def restore_stored_backup(name: str):
    form = ConfirmForm()
    if not form.validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(url_for("settings.index"))

    mode = request.form.get("mode", "merge")
    if mode == "replace" and request.form.get("confirmation", "").strip().upper() != "SUBSTITUIR":
        flash("Para substituir todos os dados, digite SUBSTITUIR para confirmar.", "error")
        return redirect(url_for("settings.index"))

    try:
        report = restore_backup(resolve_backup(name), mode=mode)
    except ServiceError as error:
        flash(error.message, "error")
        return redirect(url_for("settings.index"))

    flash(_restore_message(report), "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/backups/restaurar")
def restore_uploaded_backup():
    # A backup archive is not a video; hold it to the document ceiling.
    enforce_content_length(current_app.config["MAX_DOCUMENT_UPLOAD_BYTES"])

    form = BackupRestoreForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            if errors:
                flash(errors[0], "error")
                break
        else:
            flash("Não foi possível restaurar o backup.", "error")
        return redirect(url_for("settings.index"))

    try:
        report = restore_backup(form.file.data, mode=form.mode.data)
    except ServiceError as error:
        flash(error.message, "error")
        return redirect(url_for("settings.index"))

    flash(_restore_message(report), "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/restaurar-padroes")
def reset_defaults():
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(url_for("settings.index"))

    SettingsService.reset_to_defaults()
    flash("Configurações restauradas para os valores padrão.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/reindexar")
def reindex():
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(url_for("settings.index"))

    total = search_index.rebuild()
    flash(f"Índice de busca reconstruído com {total} documento(s).", "success")
    return redirect(url_for("settings.index"))


def _restore_message(report) -> str:
    parts = [f"{report.documents_created} documento(s) restaurado(s)"]
    if report.documents_skipped:
        parts.append(f"{report.documents_skipped} já existente(s) mantido(s)")
    if report.groups_created:
        parts.append(f"{report.groups_created} grupo(s) recriado(s)")
    if report.goals_created:
        parts.append(f"{report.goals_created} meta(s) restaurada(s)")
    if report.goal_templates_created:
        parts.append(f"{report.goal_templates_created} predefinida(s)")
    if report.phrases_created:
        parts.append(f"{report.phrases_created} frase(s)")
    if report.achievements_restored:
        parts.append(f"{report.achievements_restored} conquista(s)")
    if report.safety_backup:
        parts.append(f"backup de segurança: {report.safety_backup}")
    if report.warnings:
        parts.append(f"{len(report.warnings)} aviso(s)")
    return ". ".join(parts) + "."
