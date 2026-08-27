"""Especificación ejecutable del lote 2 — vault y watcher (#36, #37, #38, #57).

Escritos **contra la spec**, no contra la implementación: cada test describe lo
que el bot DEBE hacer. Los escribió un agente sin permiso para tocar `adso/`, y
la implementación la hace otro que no puede tocar estos tests.

Este lote toca la capa que escribe al disco, así que los **contra-casos** pesan
más que en el lote 1: un guard aplicado de más en la limpieza de wikilinks borra
links buenos (es literalmente el bug de #3), y un "arreglo" del dedup de
adjuntos duplica binarios de 1,5 MB en la SD de la RPi4. Los tests sin marca
`xfail` son esos contra-casos: pasan hoy y tienen que seguir pasando después.

Issues:
  #36 — `save_resource`: I/O bloqueante en el event loop, ventana TOCTOU al
        reservar el nombre y copia no atómica (adjunto truncado en Resources).
  #37 — durabilidad de la escritura de notas: placeholder vacío que sobrevive a
        una escritura fallida, y entrada de directorio sin sincronizar.
  #38 — el watcher se frena esperando el `send_message` de Telegram dentro del
        loop que drena la cola.
  #57 — los wikilinks rotos solo se reconcilian si el watcher vio el borrado, y
        `03-Resources/` acumula binarios que ninguna nota referencia.

El hilo común es la regla de oro del proyecto: **sin pérdida de datos** — y su
corolario para este lote, ante la duda mover antes que borrar.

Nota sobre determinismo (#36B): la ventana TOCTOU no se reproduce con
`asyncio.gather` a secas — el resultado depende de si el thread pool alcanza a
crear el archivo antes de que el event loop retome la otra corrutina. Acá se
fuerza el interleaving con `_GatedExecutor`: un executor por defecto que **no
ejecuta nada** hasta que el test lo ordena, de modo que las dos corrutinas
avanzan en lock-step y el resultado es siempre el mismo. Se apoya en que todo el
I/O pasa por `asyncio.to_thread` — que es justo lo que exige #36A.
"""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import os
import stat
import threading
from concurrent.futures import Future, ThreadPoolExecutor
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Andamiaje: executor gateado (determinismo de la concurrencia)
# ---------------------------------------------------------------------------


class _GatedExecutor(ThreadPoolExecutor):
    """Executor por defecto del loop que sólo corre lo que el test le ordena.

    `asyncio.to_thread` termina en `loop.run_in_executor(None, ...)`, así que
    reemplazando el executor por defecto el test controla exactamente cuándo se
    ejecuta cada tramo de I/O. `set_default_executor` exige un
    `ThreadPoolExecutor`, de ahí la herencia (nunca se arranca ningún thread:
    `submit` no delega en el padre).
    """

    def __init__(self) -> None:
        super().__init__(max_workers=1)
        self.pending: list[tuple[Future, object, tuple, dict]] = []

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[override]
        future: Future = Future()
        self.pending.append((future, fn, args, kwargs))
        return future

    def flush(self) -> int:
        """Ejecuta, en orden, todos los jobs encolados hasta este momento."""
        batch, self.pending = self.pending, []
        for future, fn, args, kwargs in batch:
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn(*args, **kwargs))
            except BaseException as exc:  # noqa: BLE001 - se propaga al await
                future.set_exception(exc)
        return len(batch)


async def _run_in_lockstep(*coros):
    """Corre varias corrutinas en lock-step, tanda de I/O por tanda de I/O.

    Cada vuelta: se deja avanzar a todas las corrutinas hasta que se bloquean en
    su próximo `to_thread`, y recién ahí se ejecutan **todos** los jobs
    pendientes juntos. Así las dos capturas eligen su nombre de archivo antes de
    que ninguna haya escrito — la condición exacta que abre la ventana TOCTOU.
    """
    loop = asyncio.get_running_loop()
    gate = _GatedExecutor()
    previo = getattr(loop, "_default_executor", None)
    loop.set_default_executor(gate)
    tasks = [asyncio.create_task(c) for c in coros]
    try:
        for _ in range(200):
            for _ in range(5):
                await asyncio.sleep(0)
            if all(t.done() for t in tasks):
                break
            if gate.pending:
                gate.flush()
            else:
                await asyncio.sleep(0.01)
        else:
            for t in tasks:
                t.cancel()
            pytest.fail("las corrutinas no terminaron: posible deadlock")
        return await asyncio.gather(*tasks)
    finally:
        loop._default_executor = previo  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Andamiaje: espías de filesystem
# ---------------------------------------------------------------------------

_FS_SYSCALLS = (
    "stat", "lstat", "mkdir", "makedirs", "open", "replace", "rename",
    "chmod", "unlink", "listdir", "scandir",
)


@contextlib.contextmanager
def _spy_fs_thread():
    """Registra en qué thread ocurre cada syscall de filesystem.

    Se parchean las funciones del módulo `os` y no las de `pathlib` porque
    `Path.exists()`, `Path.stat()` y `Path.mkdir()` resuelven `os.stat`/`os.mkdir`
    en el momento de la llamada: cubrir `os` cubre las dos formas de escribir lo
    mismo, sin atarse a cuál elige la implementación.

    Yields:
        (en_el_loop, total): listas de nombres de syscall.
    """
    hilo_loop = threading.get_ident()
    en_el_loop: list[str] = []
    total: list[str] = []

    def _wrap(nombre: str, fn):
        def _spy(*args, **kwargs):
            total.append(nombre)
            if threading.get_ident() == hilo_loop:
                en_el_loop.append(nombre)
            return fn(*args, **kwargs)
        return _spy

    with contextlib.ExitStack() as stack:
        for nombre in _FS_SYSCALLS:
            original = getattr(os, nombre)
            stack.enter_context(patch.object(os, nombre, _wrap(nombre, original)))
        yield en_el_loop, total


@contextlib.contextmanager
def _spy_fsync():
    """Registra cada `os.fsync`, distinguiendo archivos de directorios.

    El fd se puede `fstat` en el momento de la llamada (todavía está abierto),
    así que se identifica el objeto sincronizado por `(st_dev, st_ino)` — no
    hace falta que la implementación exponga nada.

    Yields:
        (dirs, files): listas de tuplas (st_dev, st_ino).
    """
    dirs: list[tuple[int, int]] = []
    files: list[tuple[int, int]] = []
    real = os.fsync

    def _spy(fd):
        try:
            st = os.fstat(fd)
            destino = dirs if stat.S_ISDIR(st.st_mode) else files
            destino.append((st.st_dev, st.st_ino))
        except OSError:
            pass
        return real(fd)

    with patch.object(os, "fsync", _spy):
        yield dirs, files


@contextlib.contextmanager
def _spy_rename():
    """Registra los destinos de `os.replace` / `os.rename`."""
    destinos: list[Path] = []
    reales = {n: getattr(os, n) for n in ("replace", "rename")}

    def _wrap(fn):
        def _spy(src, dst, **kwargs):
            destinos.append(Path(os.fsdecode(dst)))
            return fn(src, dst, **kwargs)
        return _spy

    with contextlib.ExitStack() as stack:
        for nombre, fn in reales.items():
            stack.enter_context(patch.object(os, nombre, _wrap(fn)))
        yield destinos


class _EscrituraQueFalla:
    """Archivo que acepta un `write` y después revienta con ENOSPC.

    Simula el corte a mitad de copia (disco lleno / OOM / `docker stop`) sin
    tocar la implementación: se envuelve el objeto que devuelve `open()` para el
    destino. `fileno()` levanta a propósito, para que `shutil` descarte su
    fast-path de `sendfile` (que copiaría en el kernel, sin pasar por `write`) y
    caiga al `copyfileobj` de siempre.
    """

    def __init__(self, wrapped, permitidas: int = 1) -> None:
        object.__setattr__(self, "_f", wrapped)
        object.__setattr__(self, "_n", 0)
        object.__setattr__(self, "_max", permitidas)

    def write(self, data):
        object.__setattr__(self, "_n", self._n + 1)
        if self._n > self._max:
            raise OSError(28, "No space left on device")
        return self._f.write(data)

    def fileno(self):
        raise OSError("sin fileno (test)")

    def __getattr__(self, name):
        return getattr(self._f, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self._f.__exit__(*args)


@contextlib.contextmanager
def _romper_escrituras_en(directorio: Path):
    """Hace fallar toda escritura dentro de `directorio` tras el primer chunk."""
    real_open = builtins.open
    marca = str(directorio)

    def _fake_open(file, mode="r", *args, **kwargs):
        f = real_open(file, mode, *args, **kwargs)
        if marca in str(file) and ("w" in mode or "a" in mode or "+" in mode):
            return _EscrituraQueFalla(f)
        return f

    with patch.object(builtins, "open", _fake_open):
        yield


def _visibles(directorio: Path) -> list[Path]:
    """Archivos que un usuario vería en Obsidian (los ocultos no son notas)."""
    return [p for p in directorio.iterdir() if not p.name.startswith(".")]


def _vault_vacio(raiz: Path) -> Path:
    """Crea un segundo vault con la estructura PARA (la fixture da uno solo)."""
    for d in ("00-Inbox", "01-Projects", "02-Areas", "03-Resources", "05-Archive"):
        (raiz / d).mkdir(parents=True, exist_ok=True)
    return raiz


# ===========================================================================
# #36 A — nada de I/O de filesystem en el event loop
# ===========================================================================


class TestA36AdjuntoSinIOEnElEventLoop:
    """Hoy `save_resource` hace `exists()`, `mkdir()`, `stat()` y el bucle que
    busca nombre libre **en el event loop**. En la RPi4 con SD lenta son decenas
    de ms de bot congelado por cada captura con adjunto — y el heartbeat y el
    resto de los handlers esperan detrás."""

    @pytest.mark.asyncio
    async def test_save_resource_does_no_filesystem_io_on_the_event_loop(
        self, vault_path: Path, tmp_path: Path
    ) -> None:
        from adso.vault_writer import save_resource

        origen = tmp_path / "descarga.pdf"
        origen.write_bytes(b"%PDF-1.4 contenido")

        with _spy_fs_thread() as (en_el_loop, total):
            await save_resource(origen, "paper.pdf", vault_path)

        # Si `total` estuviera vacío el test pasaría sin haber medido nada.
        assert total, "el espía no vio ninguna syscall: el andamiaje está roto"
        assert en_el_loop == [], (
            f"I/O de filesystem en el event loop: {sorted(set(en_el_loop))}"
        )


# ===========================================================================
# #36 B — la reserva del nombre no puede tener ventana TOCTOU
# ===========================================================================


class TestB36ReservaDelNombreSinTOCTOU:
    """Entre elegir el nombre libre y copiar hay varios `await`. Dos flujos
    concurrentes (una captura del usuario y el cron, o dos fotos seguidas)
    eligen el mismo candidato y la segunda copia **pisa** a la primera: el
    adjunto de la primera nota deja de existir, con el `![[...]]` apuntando a un
    binario que ya es otro."""

    @pytest.mark.asyncio
    async def test_concurrent_saves_of_different_content_keep_both_files(
        self, vault_path: Path, tmp_path: Path
    ) -> None:
        from adso.vault_writer import save_resource

        a = tmp_path / "a.pdf"
        b = tmp_path / "b.pdf"
        # Mismo tamaño a propósito: el short-circuit por tamaño del dedup no
        # alcanza a distinguirlos, tiene que decidir el hash.
        a.write_bytes(b"A" * 4096)
        b.write_bytes(b"B" * 4096)

        p1, p2 = await _run_in_lockstep(
            save_resource(a, "paper.pdf", vault_path),
            save_resource(b, "paper.pdf", vault_path),
        )

        assert p1 != p2, "las dos capturas resolvieron al mismo archivo"
        guardados = {f.read_bytes() for f in _visibles(vault_path / "03-Resources")}
        assert guardados == {b"A" * 4096, b"B" * 4096}, (
            "una copia pisó a la otra: se perdió un adjunto"
        )

    @pytest.mark.asyncio
    async def test_concurrent_saves_of_identical_content_still_dedup(
        self, vault_path: Path, tmp_path: Path
    ) -> None:
        """Contra-caso: el dedup por SHA-256 se mantiene tal cual.

        Es la trampa del fix: reservar el nombre con `O_EXCL` sin comparar el
        contenido convierte cada reenvío del mismo PDF en un binario nuevo en la
        SD (el vault real ya juntó 1,5 MB de basura por menos que eso)."""
        from adso.vault_writer import save_resource

        origen = tmp_path / "descarga.pdf"
        origen.write_bytes(b"%PDF-1.4 el mismo contenido")

        p1, p2 = await _run_in_lockstep(
            save_resource(origen, "paper.pdf", vault_path),
            save_resource(origen, "paper.pdf", vault_path),
        )

        assert p1 == p2
        assert len(_visibles(vault_path / "03-Resources")) == 1

    @pytest.mark.asyncio
    async def test_sequential_saves_of_identical_content_still_dedup(
        self, vault_path: Path, tmp_path: Path
    ) -> None:
        """Contra-caso: el dedup secuencial (el caso normal) tampoco cambia."""
        from adso.vault_writer import save_resource

        origen = tmp_path / "descarga.pdf"
        origen.write_bytes(b"%PDF-1.4 contenido")

        primero = await save_resource(origen, "paper.pdf", vault_path)
        segundo = await save_resource(origen, "paper.pdf", vault_path)

        assert primero == segundo
        assert len(list((vault_path / "03-Resources").glob("paper*.pdf"))) == 1

    @pytest.mark.asyncio
    async def test_different_files_of_the_same_size_are_not_confused(
        self, vault_path: Path, tmp_path: Path
    ) -> None:
        """Contra-caso: mismo tamaño y contenido distinto → dos archivos.

        El dedup decide por hash, no por tamaño; reservar el nombre no puede
        volver a la comparación barata."""
        from adso.vault_writer import save_resource

        a = tmp_path / "a.pdf"
        b = tmp_path / "b.pdf"
        a.write_bytes(b"AAAAAAAAAA")
        b.write_bytes(b"BBBBBBBBBB")

        p1 = await save_resource(a, "paper.pdf", vault_path)
        p2 = await save_resource(b, "paper.pdf", vault_path)

        assert p1 != p2
        assert p2.read_bytes() == b"BBBBBBBBBB"


# ===========================================================================
# #36 C — la copia del adjunto tiene que ser atómica
# ===========================================================================


class TestC36CopiaDeAdjuntoAtomica:
    """Hoy se copia directo sobre el nombre final: un corte a mitad deja en
    `03-Resources/` un PDF truncado que Obsidian lista como si estuviera bien.
    Peor que perderlo, porque nadie se entera."""

    @pytest.mark.asyncio
    async def test_interrupted_copy_leaves_no_partial_file_in_resources(
        self, vault_path: Path, tmp_path: Path
    ) -> None:
        from adso.vault_writer import save_resource

        origen = tmp_path / "grande.pdf"
        origen.write_bytes(b"X" * (512 * 1024))  # varios chunks de copia

        resources = vault_path / "03-Resources"
        with _romper_escrituras_en(resources):
            # El error se propaga: tragárselo dejaría al usuario creyendo que el
            # adjunto quedó guardado, que es la falla que la regla del proyecto
            # ("no silenciar excepciones de escritura") prohíbe explícitamente.
            with pytest.raises(OSError):
                await save_resource(origen, "paper.pdf", vault_path)

        assert _visibles(resources) == [], (
            "quedó un adjunto parcial visible en 03-Resources: "
            f"{[(p.name, p.stat().st_size) for p in _visibles(resources)]}"
        )

    @pytest.mark.asyncio
    async def test_copy_is_published_with_an_atomic_rename(
        self, vault_path: Path, tmp_path: Path
    ) -> None:
        """El nombre final tiene que aparecer de una, por rename.

        Limpiar el parcial en un `except` no alcanza: cubre la excepción
        atrapable pero no el corte de luz ni el OOM killer, que son justo los
        casos que menciona el issue. La única forma de que el adjunto nunca se
        vea a medio escribir es publicarlo con un rename atómico."""
        from adso.vault_writer import save_resource

        origen = tmp_path / "descarga.pdf"
        origen.write_bytes(b"%PDF-1.4 contenido")

        with _spy_rename() as destinos:
            dest = await save_resource(origen, "paper.pdf", vault_path)

        assert dest in destinos, (
            f"el adjunto no se publicó con un rename atómico (renames: {destinos})"
        )

    @pytest.mark.asyncio
    async def test_happy_path_keeps_exact_content_and_0644(
        self, vault_path: Path, tmp_path: Path
    ) -> None:
        """Contra-caso: el camino feliz no cambia — bytes exactos y 0644.

        Los 0644 los arregló G4 después de que todos los adjuntos quedaran 0600
        (ilegibles para Syncthing corriendo con otro UID). Escribir a un temporal
        y renombrar es justo el camino por el que se volvía a colar el 0600."""
        from adso.vault_writer import save_resource

        origen = tmp_path / "descarga.pdf"
        origen.write_bytes(b"%PDF-1.4 contenido exacto")
        origen.chmod(0o600)  # como lo deja tempfile

        dest = await save_resource(origen, "paper.pdf", vault_path)

        assert dest.read_bytes() == b"%PDF-1.4 contenido exacto"
        assert stat.S_IMODE(dest.stat().st_mode) == 0o644, (
            f"quedó {oct(stat.S_IMODE(dest.stat().st_mode))}"
        )


# ===========================================================================
# #37 A — sin placeholders fantasma
# ===========================================================================


def _fm(titulo: str) -> dict:
    return {"title": titulo, "type": "reference", "status": "active"}


class TestA37SinPlaceholderFantasma:
    """La reserva del nombre crea un archivo vacío y después escribe el
    contenido. Si la escritura falla (disco lleno, I/O de la SD), el vacío
    **queda**: se commitea al backup, dispara el watcher y aparece para siempre
    como una nota en blanco. Y encima ocupa el nombre, así que el reintento
    escribe `-2`.

    El fallo se inyecta en `os.fsync`, que toda escritura durable tiene que
    llamar (lo exige #37B en esta misma spec), para no atarse a la forma exacta
    de la escritura.
    """

    @pytest.mark.asyncio
    async def test_failed_write_leaves_no_empty_note(self, vault_path: Path) -> None:
        from adso.vault_writer import create_note

        with patch.object(os, "fsync", side_effect=OSError(28, "No space left on device")):
            # El fallo de escritura llega al caller: `_cb_confirm` lo necesita
            # para conservar el estado y que un segundo [Confirmar] reintente.
            with pytest.raises(OSError):
                await create_note(_fm("Nota que no se pudo escribir"), "cuerpo", vault_path)

        quedaron = [p for p in vault_path.rglob("*.md") if not p.name.startswith(".")]
        assert quedaron == [], (
            f"quedó una nota fantasma: {[(p.name, p.stat().st_size) for p in quedaron]}"
        )

    @pytest.mark.asyncio
    async def test_retry_after_failure_reuses_the_same_filename(
        self, vault_path: Path, tmp_path: Path
    ) -> None:
        """El nombre esperado se saca de un vault limpio en vez de recalcularlo
        acá: así el test no depende de cómo se arma el nombre de archivo."""
        from adso.vault_writer import create_note

        limpio = _vault_vacio(tmp_path / "vault-de-referencia")
        esperado = (await create_note(_fm("Misma nota"), "cuerpo", limpio)).name

        with patch.object(os, "fsync", side_effect=OSError(28, "No space left on device")):
            with pytest.raises(OSError):
                await create_note(_fm("Misma nota"), "cuerpo", vault_path)

        reintento = await create_note(_fm("Misma nota"), "cuerpo", vault_path)

        assert reintento.name == esperado, (
            f"el reintento no pudo reusar el nombre ({reintento.name} != {esperado})"
        )
        assert reintento.read_text(encoding="utf-8").rstrip().endswith("cuerpo")

    @pytest.mark.asyncio
    async def test_happy_path_writes_exactly_one_note(self, vault_path: Path) -> None:
        """Contra-caso: sin fallo, una sola nota, con su contenido y 0644."""
        from adso.vault_writer import create_note

        path = await create_note(_fm("Nota feliz"), "el cuerpo", vault_path)

        assert [p for p in vault_path.rglob("*.md") if not p.name.startswith(".")] == [path]
        contenido = path.read_text(encoding="utf-8")
        assert "el cuerpo" in contenido
        assert "Nota feliz" in contenido
        assert stat.S_IMODE(path.stat().st_mode) == 0o644

    @pytest.mark.asyncio
    async def test_happy_path_leaves_no_temp_files(self, vault_path: Path) -> None:
        """Contra-caso: tampoco quedan temporales ocultos dando vueltas."""
        from adso.vault_writer import create_note

        await create_note(_fm("Nota feliz"), "el cuerpo", vault_path)

        sueltos = [p for p in vault_path.rglob("*") if p.is_file() and p.name.startswith(".")]
        assert sueltos == [], f"temporales sin limpiar: {sueltos}"


# ===========================================================================
# #37 B — durabilidad ante corte de energía
# ===========================================================================


class TestB37DurabilidadDelDirectorio:
    """Se sincroniza el archivo pero no la entrada de directorio: un corte de
    luz pocos segundos después de "Nota guardada" puede evaporar el rename y
    dejar el vault sin la nota, con el usuario convencido de que la guardó. La
    RPi4 no tiene UPS, así que es el modo de fallo más probable de todos."""

    @pytest.mark.asyncio
    async def test_directory_entry_is_fsynced(self, vault_path: Path) -> None:
        from adso.vault_writer import create_note

        with _spy_fsync() as (dirs, files):
            path = await create_note(_fm("Nota durable"), "cuerpo", vault_path)

        st = os.stat(path.parent)
        assert (st.st_dev, st.st_ino) in dirs, (
            "no se sincronizó la entrada de directorio: el rename puede evaporarse"
        )

    @pytest.mark.asyncio
    async def test_directory_entry_is_fsynced_on_every_write_path(
        self, vault_path: Path
    ) -> None:
        """La garantía tiene que vivir en el helper de escritura atómica.

        Si el fsync del directorio se pone sólo en `create_note`, la ventana de
        pérdida sigue abierta en `append_to_note`, `set_property` y la
        actualización de wikilinks — que también renombran sobre el vault. Este
        test mide `append_to_note`, con el espía activo **sólo** durante el
        append para que no lo tape el fsync de la creación."""
        from adso.vault_writer import append_to_note, create_note

        path = await create_note(_fm("Nota que crece"), "cuerpo", vault_path)

        with _spy_fsync() as (dirs, files):
            await append_to_note(path, "más cuerpo")

        st = os.stat(path.parent)
        assert (st.st_dev, st.st_ino) in dirs, (
            "append_to_note no sincroniza el directorio: el fsync quedó atado a "
            "create_note en vez de vivir en la escritura atómica compartida"
        )

    @pytest.mark.asyncio
    async def test_note_file_itself_is_still_fsynced(self, vault_path: Path) -> None:
        """Contra-caso: la garantía que ya existía (fsync del contenido) sigue."""
        from adso.vault_writer import create_note

        with _spy_fsync() as (dirs, files):
            await create_note(_fm("Nota durable"), "cuerpo", vault_path)

        assert files, "dejó de sincronizarse el contenido del archivo"


# ===========================================================================
# #38 — el watcher no puede frenarse notificando
# ===========================================================================


def _make_watcher(tmp_path: Path, **kwargs):
    """Watcher listo para tests, con el observer de watchdog mockeado."""
    from adso.vault_watcher import VaultWatcher

    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    bot = MagicMock()
    bot.send_message = AsyncMock()
    watcher = VaultWatcher(
        vault_path=vault, bot=bot, chat_id=12345, **kwargs
    )
    return watcher, bot, vault


def _conflicto(vault: Path, n: int) -> Path:
    return vault / f"nota{n}.sync-conflict-20260826-12000{n}-ABCD1234.md"


class Test38WatcherNoSeFrenaNotificando:
    """Las notificaciones se esperan **dentro** del loop que drena la cola. Un
    `send_message` lento (timeout de PTB: ~5 s) frena el drenado justo en el
    peor momento: una ráfaga de Syncthing, que es cuando más eventos llegan y
    cuando el usuario más necesita que el re-embed vaya al día."""

    @pytest.mark.asyncio
    async def test_slow_notification_does_not_stall_the_queue(
        self, tmp_path: Path
    ) -> None:
        from adso.vault_watcher import _VaultEvent

        watcher, bot, vault = _make_watcher(tmp_path)
        release = asyncio.Event()
        iniciados: list[dict] = []

        async def _envio_lento(**kwargs):
            iniciados.append(kwargs)
            await release.wait()

        bot.send_message = AsyncMock(side_effect=_envio_lento)

        with patch("adso.vault_watcher._make_observer", return_value=MagicMock()):
            await watcher.start()
            for i in range(3):
                await watcher._queue.put(
                    _VaultEvent(path=_conflicto(vault, i), is_conflict=True)
                )
            await asyncio.sleep(0.05)

            try:
                assert watcher.stats.conflicts_detected == 3, (
                    "el watcher dejó de procesar la cola esperando a Telegram"
                )
                assert len(iniciados) == 3, (
                    "las notificaciones se serializan: la segunda espera a la primera"
                )
            finally:
                release.set()
                await watcher.stop()

    @pytest.mark.asyncio
    async def test_pending_notifications_are_drained_on_stop(
        self, tmp_path: Path
    ) -> None:
        """Lanzarlas en background no puede degenerar en tareas huérfanas: al
        parar, todas tienen que estar esperadas (nada de "Task was destroyed but
        it is pending", que además se lleva puesto el flush del git backup)."""
        from adso.vault_watcher import _VaultEvent

        watcher, bot, vault = _make_watcher(tmp_path)
        release = asyncio.Event()
        iniciados: list[dict] = []
        terminados: list[dict] = []

        async def _envio_lento(**kwargs):
            iniciados.append(kwargs)
            await release.wait()
            terminados.append(kwargs)

        bot.send_message = AsyncMock(side_effect=_envio_lento)
        tareas_previas = asyncio.all_tasks()

        with patch("adso.vault_watcher._make_observer", return_value=MagicMock()):
            await watcher.start()
            for i in range(2):
                await watcher._queue.put(
                    _VaultEvent(path=_conflicto(vault, i), is_conflict=True)
                )
            await asyncio.sleep(0.05)

            try:
                assert len(iniciados) == 2, (
                    "la segunda notificación ni siquiera arrancó: el loop está bloqueado"
                )
            finally:
                release.set()
                await watcher.stop()

        assert len(terminados) == 2, "una notificación en vuelo se perdió al parar"
        colgadas = {t for t in asyncio.all_tasks() - tareas_previas if not t.done()}
        assert not colgadas, f"quedaron tareas pendientes tras stop(): {colgadas}"

    @pytest.mark.asyncio
    async def test_conflict_is_still_notified(self, tmp_path: Path) -> None:
        """Contra-caso: no frenarse no puede significar dejar de avisar."""
        from adso.vault_watcher import _VaultEvent

        watcher, bot, vault = _make_watcher(tmp_path)

        with patch("adso.vault_watcher._make_observer", return_value=MagicMock()):
            await watcher.start()
            await watcher._queue.put(
                _VaultEvent(path=_conflicto(vault, 1), is_conflict=True)
            )
            await asyncio.sleep(0.05)
            await watcher.stop()

        assert bot.send_message.await_count == 1
        texto = bot.send_message.await_args.kwargs["text"]
        assert "sync-conflict-20260826-120001-ABCD1234" in texto

    @pytest.mark.asyncio
    async def test_dedup_window_still_collapses_duplicate_changes(
        self, tmp_path: Path
    ) -> None:
        """Contra-caso: la deduplicación de 2 s por path se mantiene.

        inotify dispara CREATE + MODIFY al escribir un archivo nuevo; si las
        notificaciones pasan a lanzarse en background sin pasar por el dedup, el
        usuario recibe el doble de mensajes por cada save."""
        from adso.vault_watcher import _VaultEvent

        cambios = AsyncMock()
        watcher, bot, vault = _make_watcher(
            tmp_path, debug=True, on_external_change=cambios
        )

        with patch("adso.vault_watcher._make_observer", return_value=MagicMock()):
            await watcher.start()
            nota = vault / "00-Inbox" / "nota.md"
            await watcher._queue.put(_VaultEvent(path=nota, is_conflict=False))
            await watcher._queue.put(_VaultEvent(path=nota, is_conflict=False))
            await asyncio.sleep(0.05)

            try:
                assert bot.send_message.await_count == 1, "notificó el cambio dos veces"
                assert cambios.await_count == 1
            finally:
                await watcher.stop()


# ===========================================================================
# #57 A — reconciliación de wikilinks en el reindex nocturno
# ===========================================================================


async def _correr_reindex(context) -> MagicMock:
    """Corre el job nocturno con un cliente de embeddings mockeado.

    El job devuelve temprano si no hay embeddings, así que se le pasa uno; el
    mock además sirve para verificar que la reconciliación se apoya en el
    recorrido que el job ya hace y no en llamadas nuevas a la red.
    """
    from adso.handlers import jobs

    emb = MagicMock()
    emb.reindex_vault = AsyncMock(
        return_value={"indexed": 0, "skipped": 0, "removed": 0, "errors": 0}
    )
    context.bot_data["embeddings"] = emb
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()

    await jobs.reindex_job(context)
    return emb


def _envejecer(path: Path, dias: int = 30) -> None:
    """Retrasa el mtime de un archivo, para que parezca lo que es: viejo."""
    viejo = time.time() - dias * 86400
    os.utime(path, (viejo, viejo))


def _nota(path: Path, cuerpo: str, titulo: str = "Nota") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {titulo}\ntype: reference\nstatus: active\n---\n\n{cuerpo}",
        encoding="utf-8",
    )
    return path


class TestA57WikilinksRotosEnElReindex:
    """La limpieza corre sólo desde el evento de borrado del watcher. Si la nota
    se borra con el contenedor parado, o desde otro dispositivo mientras ADSO
    está caído, inotify nunca dispara y el link queda roto para siempre: así es
    como el vault real acumuló 14."""

    @pytest.mark.asyncio
    async def test_nightly_reindex_removes_broken_wikilinks(
        self, mock_context, vault_path: Path
    ) -> None:
        _nota(vault_path / "00-Inbox" / "existe.md", "soy el destino vivo", "Existe")
        nota = _nota(
            vault_path / "00-Inbox" / "con-links.md",
            "Cuerpo.\n\n## Ver también\n\n"
            "- [[fantasma]] — Borrada con el bot apagado\n"
            "- [[existe]] — Sigue viva\n",
            "Con links",
        )

        emb = await _correr_reindex(mock_context)

        contenido = nota.read_text(encoding="utf-8")
        assert "[[fantasma]]" not in contenido, "el link roto sobrevivió al reindex"
        assert "[[existe]]" in contenido, "se llevó puesto un link bueno"
        assert "Cuerpo." in contenido
        # La reconciliación se apoya en el recorrido que el job ya hace: no
        # puede costar una pasada extra de embeddings (red + quota).
        assert emb.reindex_vault.await_count == 1

    @pytest.mark.asyncio
    async def test_reconciliation_runs_without_an_embeddings_client(
        self, mock_context, vault_path: Path
    ) -> None:
        """La reconciliación es trabajo local: no depende del índice.

        Hoy `reindex_job` retorna apenas ve que no hay cliente de embeddings, y
        con eso la limpieza de links y la barrida de adjuntos no corren **nunca**
        justo en el escenario donde más falta hacen: índice caído o mal
        configurado, vault acumulando links rotos noche tras noche. El re-embed
        sí depende del cliente; esto no."""
        from adso.handlers import jobs

        nota = _nota(
            vault_path / "00-Inbox" / "con-links.md",
            "Cuerpo.\n\n## Ver también\n\n- [[fantasma]] — Borrada hace meses\n",
            "Con links",
        )
        huerfano = vault_path / "03-Resources" / "huerfano.pdf"
        huerfano.write_bytes(b"%PDF-1.4 nadie me referencia")
        _envejecer(huerfano)

        mock_context.bot_data["embeddings"] = None
        mock_context.bot = MagicMock()
        mock_context.bot.send_message = AsyncMock()

        await jobs.reindex_job(mock_context)

        assert "[[fantasma]]" not in nota.read_text(encoding="utf-8"), (
            "sin embeddings no se reconciliaron los links rotos"
        )
        assert not huerfano.exists(), (
            "sin embeddings no se barrieron los adjuntos huérfanos"
        )
        assert list((vault_path / "05-Archive").rglob("huerfano.pdf"))

    @pytest.mark.asyncio
    async def test_wikilink_to_existing_note_is_kept(
        self, mock_context, vault_path: Path
    ) -> None:
        """Contra-caso: un destino que existe no se toca."""
        _nota(vault_path / "00-Inbox" / "existe.md", "destino", "Existe")
        nota = _nota(
            vault_path / "00-Inbox" / "con-links.md",
            "Cuerpo.\n\n## Ver también\n\n- [[existe]] — Sigue viva\n",
            "Con links",
        )

        await _correr_reindex(mock_context)

        assert "[[existe]]" in nota.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_wikilink_to_moved_note_survives(
        self, mock_context, vault_path: Path
    ) -> None:
        """Contra-caso crítico: los wikilinks resuelven por stem, no por path.

        Es exactamente el bug de #3, arreglado hoy: mover una nota a otra carpeta
        no rompe el link, así que la reconciliación no puede borrarlo sólo
        porque el destino ya no esté en el directorio del que lo linkea."""
        _nota(
            vault_path / "01-Projects" / "tesis" / "movida.md",
            "me mudé de carpeta",
            "Movida",
        )
        nota = _nota(
            vault_path / "00-Inbox" / "linkea.md",
            "Cuerpo.\n\n## Ver también\n\n- [[movida]] — Se mudó a 01-Projects\n",
            "Linkea",
        )

        await _correr_reindex(mock_context)

        assert "[[movida]]" in nota.read_text(encoding="utf-8"), (
            "borró el link de una nota movida (regresión de #3)"
        )

    @pytest.mark.asyncio
    async def test_wikilink_inside_code_fence_is_kept(
        self, mock_context, vault_path: Path
    ) -> None:
        """Contra-caso: dentro de un bloque de código no hay links (#5)."""
        nota = _nota(
            vault_path / "00-Inbox" / "con-codigo.md",
            "Cuerpo.\n\n## Ver también\n\n"
            "```text\n- [[ejemplo-de-sintaxis]]\n```\n",
            "Con código",
        )

        await _correr_reindex(mock_context)

        assert "[[ejemplo-de-sintaxis]]" in nota.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_wikilink_to_archived_note_survives(
        self, mock_context, vault_path: Path
    ) -> None:
        """Contra-caso crítico: el criterio es "¿lo resuelve Obsidian?", no
        "¿está indexado?".

        `05-Archive/` está excluida del índice semántico (`vault.exclude_dirs`),
        pero el archivo existe y Obsidian abre el link. Reconciliar mirando el
        índice en vez del disco borraría todos los links a notas archivadas de
        una sola pasada nocturna — archivar no es borrar. Es el mismo criterio
        que arregló #3 en la otra dirección: manda la existencia en disco."""
        _nota(
            vault_path / "05-Archive" / "proyecto-viejo" / "archivada.md",
            "me archivaron, no me borraron",
            "Archivada",
        )
        nota = _nota(
            vault_path / "00-Inbox" / "linkea.md",
            "Cuerpo.\n\n## Ver también\n\n- [[archivada]] — Proyecto cerrado\n",
            "Linkea",
        )

        await _correr_reindex(mock_context)

        assert "[[archivada]]" in nota.read_text(encoding="utf-8"), (
            "borró el link a una nota archivada: pérdida de datos"
        )

    @pytest.mark.asyncio
    async def test_embed_of_existing_resource_survives(
        self, mock_context, vault_path: Path
    ) -> None:
        """Contra-caso crítico: un embed a `03-Resources/` no es un link roto.

        `03-Resources/` tampoco está en el índice, y sus archivos no son `.md`:
        una reconciliación que sólo mire notas indexadas se llevaría puesto el
        `![[paper.pdf]]` de toda nota con adjunto — y con él, la única
        referencia que mantiene vivo al binario en la barrida de #57B."""
        (vault_path / "03-Resources" / "paper.pdf").write_bytes(b"%PDF-1.4 existo")
        nota = _nota(
            vault_path / "00-Inbox" / "con-adjunto.md",
            "Cuerpo.\n\n![[paper.pdf]]\n\n## Ver también\n\n- [[paper.pdf]] — El PDF\n",
            "Con adjunto",
        )

        await _correr_reindex(mock_context)

        contenido = nota.read_text(encoding="utf-8")
        assert "![[paper.pdf]]" in contenido, "borró el embed de un adjunto que existe"
        assert "- [[paper.pdf]]" in contenido

    @pytest.mark.asyncio
    async def test_clean_note_is_not_rewritten(
        self, mock_context, vault_path: Path
    ) -> None:
        """Contra-caso: una nota sin links rotos no se reescribe.

        Reescribirla sin cambio real bumpea el mtime → evento del watcher →
        re-embed espurio (llamada a Gemini) + churn del backup, por cada nota
        del vault y cada noche (F11)."""
        _nota(vault_path / "00-Inbox" / "existe.md", "destino", "Existe")
        nota = _nota(
            vault_path / "00-Inbox" / "limpia.md",
            "Cuerpo.\n\n## Ver también\n\n- [[existe]] — Sigue viva\n",
            "Limpia",
        )
        mtime = nota.stat().st_mtime_ns

        await _correr_reindex(mock_context)

        assert nota.stat().st_mtime_ns == mtime, (
            "reescribió una nota que no cambió → re-embed espurio"
        )


# ===========================================================================
# #57 B — 03-Resources no acumula binarios sin dueño
# ===========================================================================


class TestB57AdjuntosHuerfanos:
    """Al borrar una nota se limpian los wikilinks de las demás, pero nadie mira
    su adjunto: el binario queda en `03-Resources/` para siempre. El vault real
    juntó 1,5 MB así. Se **archiva**, no se borra: un PDF sin nota puede seguir
    siendo valioso y no hay forma de recuperarlo si nos equivocamos."""

    @pytest.mark.asyncio
    async def test_orphan_attachment_is_moved_to_archive(
        self, mock_context, vault_path: Path
    ) -> None:
        huerfano = vault_path / "03-Resources" / "huerfano.pdf"
        huerfano.write_bytes(b"%PDF-1.4 nadie me referencia")
        # Un huérfano real es viejo: el binario quedó sin dueño cuando se borró
        # su nota, hace días o meses. Los del vault de producción tenían meses.
        # Envejecerlo es lo que hace realista al escenario — un archivo escrito
        # recién es indistinguible de una captura a medio confirmar, y para ese
        # caso está el contra-caso de abajo.
        _envejecer(huerfano)
        _nota(vault_path / "00-Inbox" / "otra.md", "no menciono ningún adjunto", "Otra")

        await _correr_reindex(mock_context)

        assert not huerfano.exists(), "el adjunto huérfano sigue en 03-Resources"
        archivados = list((vault_path / "05-Archive").rglob("huerfano.pdf"))
        assert archivados, "se borró en vez de archivarse (regla de oro)"
        assert archivados[0].read_bytes() == b"%PDF-1.4 nadie me referencia"

    @pytest.mark.asyncio
    async def test_freshly_written_attachment_is_not_archived(
        self, mock_context, vault_path: Path
    ) -> None:
        """Contra-caso: un adjunto recién escrito no puede archivarse.

        `_cb_confirm` guarda el binario con `save_resource` y **después** escribe
        la nota que lo referencia. Si la barrida nocturna cae en ese hueco, ve un
        binario sin dueño y se lo lleva — dejando el embed roto en una nota que
        acababa de nacer. Es improbable (requiere confirmar una captura con
        adjunto justo durante el reindex) pero el guard es gratis y el daño es
        silencioso.
        """
        recien = vault_path / "03-Resources" / "recien-guardado.pdf"
        recien.write_bytes(b"%PDF-1.4 mi nota esta por nacer")
        _nota(vault_path / "00-Inbox" / "otra.md", "todavia no lo referencio", "Otra")

        await _correr_reindex(mock_context)

        assert recien.exists(), (
            "se archivó un adjunto recién escrito: si era una captura en vuelo, "
            "su nota queda con el embed roto"
        )

    @pytest.mark.asyncio
    async def test_attachment_referenced_in_frontmatter_is_kept(
        self, mock_context, vault_path: Path
    ) -> None:
        """Contra-caso crítico: `source_file` cuenta como referencia."""
        adjunto = vault_path / "03-Resources" / "paper.pdf"
        adjunto.write_bytes(b"%PDF-1.4 tengo duenio")
        (vault_path / "00-Inbox" / "nota.md").write_text(
            "---\ntitle: Nota\ntype: reference\nstatus: active\n"
            'source_file: "[[paper.pdf]]"\n---\n\nCuerpo.\n',
            encoding="utf-8",
        )

        await _correr_reindex(mock_context)

        assert adjunto.exists(), "archivó un adjunto referenciado en el frontmatter"

    @pytest.mark.asyncio
    async def test_attachment_embedded_in_body_is_kept(
        self, mock_context, vault_path: Path
    ) -> None:
        """Contra-caso crítico: el embed `![[archivo]]` también cuenta.

        Una nota editada a mano puede conservar sólo una de las dos formas."""
        adjunto = vault_path / "03-Resources" / "imagen.png"
        adjunto.write_bytes(b"\x89PNG soy una captura")
        _nota(
            vault_path / "00-Inbox" / "nota.md",
            "Cuerpo.\n\n![[imagen.png]]\n",
            "Nota",
        )

        await _correr_reindex(mock_context)

        assert adjunto.exists(), "archivó un adjunto embebido en el body"

    @pytest.mark.asyncio
    async def test_shared_attachment_survives_deletion_of_one_note(
        self, mock_context, vault_path: Path
    ) -> None:
        """Contra-caso crítico: el dedup por hash hace que varias notas compartan
        el mismo binario. Se archiva sólo cuando **ninguna** lo referencia; que
        se haya borrado una de las dos notas no alcanza."""
        adjunto = vault_path / "03-Resources" / "compartido.pdf"
        adjunto.write_bytes(b"%PDF-1.4 me comparten dos notas")
        borrada = _nota(
            vault_path / "00-Inbox" / "una.md",
            "Cuerpo.\n\n![[compartido.pdf]]\n",
            "Una",
        )
        _nota(
            vault_path / "01-Projects" / "tesis" / "otra.md",
            "Cuerpo.\n\n![[compartido.pdf]]\n",
            "Otra",
        )
        borrada.unlink()  # borrada con el bot apagado

        await _correr_reindex(mock_context)

        assert adjunto.exists(), (
            "archivó un binario que la nota sobreviviente sigue referenciando"
        )

    @pytest.mark.asyncio
    async def test_two_orphans_with_the_same_name_do_not_overwrite_each_other(
        self, mock_context, vault_path: Path
    ) -> None:
        """Dos huérfanos homónimos archivados en corridas distintas conviven.

        `03-Resources/` desambigua con sufijo numérico, pero dos adjuntos que
        llegaron con el mismo nombre en momentos distintos (`imagen.png` de dos
        capturas, dedupeadas a archivos distintos por hash) pueden quedar
        huérfanos en noches distintas y aterrizar en el mismo destino. Pisar el
        primero sería perder el binario justo en el paso que existe para no
        perderlo."""
        primero = vault_path / "03-Resources" / "huerfano.pdf"
        primero.write_bytes(b"%PDF-1.4 el primer huerfano")
        _envejecer(primero)
        await _correr_reindex(mock_context)

        segundo = vault_path / "03-Resources" / "huerfano.pdf"
        segundo.write_bytes(b"%PDF-1.4 el segundo huerfano")
        _envejecer(segundo)
        await _correr_reindex(mock_context)

        archivados = list((vault_path / "05-Archive").rglob("huerfano*.pdf"))
        assert len(archivados) == 2, (
            f"el segundo huérfano pisó al primero: {[p.name for p in archivados]}"
        )
        assert {p.read_bytes() for p in archivados} == {
            b"%PDF-1.4 el primer huerfano",
            b"%PDF-1.4 el segundo huerfano",
        }
