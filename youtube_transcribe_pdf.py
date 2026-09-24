#!/usr/bin/env python3
"""
youtube_transcribe_pdf.py

Baixa o áudio de um vídeo do YouTube, transcreve com faster-whisper
e gera um PDF com a transcrição.

Dependências:
    pip install yt-dlp faster-whisper reportlab tqdm psutil

Requer também o binário `ffmpeg` instalado no sistema (yt-dlp e whisper
dependem dele para extrair/decodificar áudio):
    Ubuntu/Debian: sudo apt install ffmpeg
    macOS:         brew install ffmpeg
    Windows:       baixar de ffmpeg.org e colocar no PATH

Uso:
    python youtube_transcribe_pdf.py "https://youtube.com/watch?v=XXXX"
    python youtube_transcribe_pdf.py "URL" --model medium --lang pt --output saida.pdf

Trade-off real de modelo (CPU, sem GPU CUDA):
    tiny/base  -> rápido, muitos erros, ok para triagem
    small      -> equilíbrio razoável para uso doméstico em CPU
    medium     -> qualidade boa, mas 5-10x o tempo do vídeo em CPU
    large-v3   -> melhor qualidade, impraticável em CPU para vídeos longos
Com GPU NVIDIA (CUDA), 'medium' ou 'large-v3' ficam viáveis mesmo para
vídeos de 1h+.

Log:
    Tudo é registrado em transcricao.log, na pasta raiz do projeto (mesmo
    diretório de onde o script é executado), além de aparecer no terminal
    enquanto a sessão estiver conectada. Se o processo morrer sem você
    estar olhando o terminal (sessão SSH caiu, PC desligou, etc.), o log
    em arquivo é a única fonte de verdade sobre o que aconteceu.
"""

import argparse
import atexit
import logging
import os
import signal
import sys
import traceback
from pathlib import Path

import psutil
from tqdm import tqdm
from yt_dlp import YoutubeDL
from faster_whisper import WhisperModel
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_JUSTIFY

LOG_PATH = Path(__file__).resolve().parent / "transcricao.log"

logger = logging.getLogger("youtube_transcribe_pdf")
logger.setLevel(logging.DEBUG)

_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)

_file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(_formatter)
logger.addHandler(_file_handler)

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(_formatter)
logger.addHandler(_console_handler)


def matar_processos_filhos() -> None:
    """Mata recursivamente todo processo filho do script atual (ex.: ffmpeg
    disparado pelo yt-dlp). Chamado ao interromper (Ctrl+C) ou ao sair por
    qualquer motivo, pra não deixar nada órfão consumindo CPU/rede em
    segundo plano.
    """
    try:
        proc_atual = psutil.Process(os.getpid())
    except psutil.NoSuchProcess:
        return
    filhos = proc_atual.children(recursive=True)
    for filho in filhos:
        try:
            filho.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if filhos:
        logger.info("Processos filhos encerrados: %s", [f.pid for f in filhos])


def _handler_interrupcao(sig, frame):
    logger.warning("Interrompido pelo usuário (sinal %s). Encerrando processos filhos...", sig)
    matar_processos_filhos()
    sys.exit(130)


signal.signal(signal.SIGINT, _handler_interrupcao)
signal.signal(signal.SIGTERM, _handler_interrupcao)
atexit.register(matar_processos_filhos)


def obter_info_video(url: str) -> dict:
    """Busca só os metadados do vídeo (sem baixar), pra saber o id/título
    antes de decidir se precisa baixar o áudio de fato."""
    ydl_opts_info = {"quiet": True, "no_warnings": True, "skip_download": True}
    with YoutubeDL(ydl_opts_info) as ydl:
        return ydl.extract_info(url, download=False)


def baixar_audio(url: str, destino_dir: Path) -> tuple[Path, dict]:
    """Baixa apenas o áudio do vídeo em formato mp3 e retorna o caminho + metadados.

    Se o mp3 já existir em destino_dir (baixado em execução anterior), pula
    o download e a extração por completo — apenas reutiliza o arquivo.
    O áudio é salvo permanentemente (não é apagado ao final), para permitir
    reprocessar a transcrição sem baixar de novo.
    """
    destino_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Buscando metadados do vídeo...")
    info = obter_info_video(url)
    caminho_audio = destino_dir / f"{info['id']}.mp3"

    if caminho_audio.exists():
        logger.info("Áudio já existe em %s, pulando download.", caminho_audio)
        return caminho_audio, info

    template_saida = str(destino_dir / "%(id)s.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": template_saida,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "quiet": True,
        "no_warnings": True,
        "logger": _YtDlpLoggerAdapter(),
    }

    logger.info("Baixando áudio de: %s", url)
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    if not caminho_audio.exists():
        raise FileNotFoundError(
            f"Download concluído mas arquivo esperado não encontrado: {caminho_audio}"
        )

    logger.info("Áudio salvo em: %s (%.1f MB)", caminho_audio, caminho_audio.stat().st_size / 1_048_576)
    return caminho_audio, info


class _YtDlpLoggerAdapter:
    """Redireciona as mensagens internas do yt-dlp para o logger do script,
    em vez de imprimir direto no stdout — assim elas também vão para o
    arquivo de log."""

    def debug(self, msg):
        if msg.startswith("[debug] "):
            logger.debug(msg)
        else:
            logger.info(msg)

    def info(self, msg):
        logger.info(msg)

    def warning(self, msg):
        logger.warning(msg)

    def error(self, msg):
        logger.error(msg)


def transcrever_audio(
    caminho_audio: Path,
    modelo: str = "small",
    idioma: str | None = "pt",
    verbose: bool = False,
) -> list[dict]:
    """
    Transcreve o áudio usando faster-whisper.
    Retorna lista de segmentos com start, end e text.

    Progresso: a barra no terminal avança conforme o timestamp de fim de
    cada segmento processado, comparado à duração total do áudio
    (info.duration). Além da barra, marcos de progresso (a cada 10%) são
    gravados no log em arquivo — a barra em si não vai para o log, porque
    é uma linha só sendo reescrita, não uma sequência de eventos.
    """
    logger.info("Carregando modelo '%s' (CPU, int8)...", modelo)
    model = WhisperModel(modelo, device="cpu", compute_type="int8")
    logger.info("Modelo '%s' carregado.", modelo)

    segmentos_iter, info = model.transcribe(
        str(caminho_audio),
        language=idioma,
        vad_filter=True,  # remove silêncio/ruído, reduz alucinação do modelo
    )

    logger.info(
        "Idioma detectado: %s (confiança: %.2f)", info.language, info.language_probability
    )
    logger.info("Duração do áudio: %s", formatar_timestamp(info.duration))

    segmentos = []
    tempo_processado = 0.0
    ultimo_marco_logado = -1

    with tqdm(
        total=info.duration,
        unit="s",
        desc="Transcrevendo",
        bar_format="{l_bar}{bar}| {n:.0f}/{total:.0f}s [{elapsed}<{remaining}]",
    ) as barra:
        for seg in segmentos_iter:
            segmentos.append(
                {"start": seg.start, "end": seg.end, "text": seg.text.strip()}
            )
            if verbose:
                tqdm.write(f"  [{seg.start:7.1f}s -> {seg.end:7.1f}s] {seg.text.strip()}")

            avanco = seg.end - tempo_processado
            if avanco > 0:
                barra.update(avanco)
                tempo_processado = seg.end

            # Marco de progresso no log (a cada 10%), independente do terminal.
            if info.duration > 0:
                marco_atual = int((tempo_processado / info.duration) * 10)
                if marco_atual > ultimo_marco_logado:
                    ultimo_marco_logado = marco_atual
                    logger.info(
                        "Progresso da transcrição: %d%% (%.0f/%.0fs)",
                        marco_atual * 10,
                        tempo_processado,
                        info.duration,
                    )

    logger.info("Transcrição concluída: %d segmentos.", len(segmentos))
    return segmentos


def formatar_timestamp(segundos: float) -> str:
    h = int(segundos // 3600)
    m = int((segundos % 3600) // 60)
    s = int(segundos % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def gerar_pdf(
    segmentos: list[dict],
    titulo: str,
    url_origem: str,
    caminho_saida: Path,
    incluir_timestamps: bool = True,
) -> None:
    """Gera um PDF com a transcrição usando reportlab (Platypus)."""
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(caminho_saida),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    estilo_corpo = ParagraphStyle(
        "Corpo",
        parent=styles["Normal"],
        fontSize=10.5,
        leading=15,
        alignment=TA_JUSTIFY,
        spaceAfter=8,
    )
    estilo_timestamp = ParagraphStyle(
        "Timestamp",
        parent=styles["Normal"],
        fontSize=8,
        textColor="#666666",
        spaceAfter=2,
    )

    story = []
    story.append(Paragraph(titulo, styles["Title"]))
    story.append(Paragraph(f"Fonte: {url_origem}", styles["Italic"]))
    story.append(Spacer(1, 16))

    for seg in segmentos:
        if incluir_timestamps:
            ts = f"[{formatar_timestamp(seg['start'])} - {formatar_timestamp(seg['end'])}]"
            story.append(Paragraph(ts, estilo_timestamp))
        # Escapa caracteres especiais de XML que o reportlab interpreta como markup
        texto_seguro = (
            seg["text"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        story.append(Paragraph(texto_seguro, estilo_corpo))

    logger.info("Gravando PDF em: %s (%d segmentos)", caminho_saida, len(segmentos))
    doc.build(story)
    logger.info("PDF gravado com sucesso: %s", caminho_saida.resolve())


def main():
    parser = argparse.ArgumentParser(
        description="Baixa áudio do YouTube, transcreve e gera PDF."
    )
    parser.add_argument("url", help="URL do vídeo do YouTube")
    parser.add_argument(
        "--model",
        default="small",
        choices=["tiny", "base", "small", "medium", "large-v3"],
        help="Modelo Whisper (padrão: small — veja trade-offs no cabeçalho do arquivo)",
    )
    parser.add_argument(
        "--lang",
        default="pt",
        help="Código do idioma (pt, en, es...) ou 'auto' para detecção automática",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Caminho do PDF de saída (padrão: <titulo_do_video>.pdf)",
    )
    parser.add_argument(
        "--no-timestamps",
        action="store_true",
        help="Não incluir marcações de tempo no PDF",
    )
    parser.add_argument(
        "--audio-dir",
        default="audios",
        help="Pasta onde o áudio baixado fica salvo (padrão: ./audios)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Imprime o texto de cada segmento conforme é transcrito (além da barra de progresso)",
    )
    args = parser.parse_args()

    idioma = None if args.lang == "auto" else args.lang
    audio_dir = Path(args.audio_dir)

    logger.info("========== Nova execução ==========")
    logger.info("URL: %s", args.url)
    logger.info("Args: model=%s lang=%s output=%s audio_dir=%s", args.model, args.lang, args.output, args.audio_dir)

    logger.info("[0/3] Verificando pastas de destino...")
    try:
        audio_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Pasta de áudio: %s", audio_dir.resolve())
        if args.output:
            pasta_saida_pdf = Path(args.output).parent
            pasta_saida_pdf.mkdir(parents=True, exist_ok=True)
            logger.info("Pasta de saída do PDF: %s", pasta_saida_pdf.resolve())
    except OSError:
        logger.exception("Falha ao criar pastas de destino.")
        sys.exit(1)

    logger.info("[1/3] Baixando áudio...")
    try:
        caminho_audio, info = baixar_audio(args.url, audio_dir)
    except Exception:
        logger.exception("Falha no download/extração do áudio.")
        sys.exit(1)

    titulo_video = info.get("title", "video")

    logger.info("[2/3] Transcrevendo (isso pode demorar dependendo do modelo/hardware)...")
    try:
        segmentos = transcrever_audio(
            caminho_audio, modelo=args.model, idioma=idioma, verbose=args.verbose
        )
    except Exception:
        logger.exception("Falha durante a transcrição.")
        sys.exit(1)

    if not segmentos:
        logger.error("Nenhum segmento de fala foi detectado. Abortando.")
        sys.exit(1)

    logger.info("[3/3] Gerando PDF...")
    nome_saida = args.output or f"{titulo_video[:80]}.pdf".replace("/", "-")
    caminho_saida = Path(nome_saida)
    try:
        gerar_pdf(
            segmentos,
            titulo=titulo_video,
            url_origem=args.url,
            caminho_saida=caminho_saida,
            incluir_timestamps=not args.no_timestamps,
        )
    except Exception:
        logger.exception("Falha ao gerar o PDF.")
        sys.exit(1)

    logger.info("Concluído: %s", caminho_saida.resolve())


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        # Rede de segurança final: qualquer exceção que escapou dos blocos
        # try/except específicos de cada etapa (bug não previsto, erro de
        # biblioteca externa, etc.) é registrada aqui antes do processo
        # morrer. Sem isso, um crash inesperado não deixa rastro nenhum —
        # foi exatamente o problema relatado antes desta versão existir.
        logger.critical("Erro não tratado, script encerrado abruptamente:\n%s", traceback.format_exc())
        sys.exit(1)