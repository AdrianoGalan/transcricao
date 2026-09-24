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
"""

import argparse
import atexit
import os
import signal
import sys
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


def matar_processos_filhos() -> None:
    """Mata recursivamente todo processo filho do script atual (ex.: ffmpeg
    disparado pelo yt-dlp). Chamado ao interromper (Ctrl+C) ou ao sair por
    qualquer motivo, pra não deixar nada órfão consumindo CPU/rede em segundo
    plano — foi exatamente isso que aconteceu nas execuções anteriores.
    """
    try:
        proc_atual = psutil.Process(os.getpid())
    except psutil.NoSuchProcess:
        return
    for filho in proc_atual.children(recursive=True):
        try:
            filho.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def _handler_interrupcao(sig, frame):
    print("\n[info] Interrompido pelo usuário (Ctrl+C). Encerrando processos filhos...")
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

    info = obter_info_video(url)
    caminho_audio = destino_dir / f"{info['id']}.mp3"

    if caminho_audio.exists():
        print(f"[info] Áudio já existe em {caminho_audio}, pulando download.")
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
        "quiet": False,
        "no_warnings": False,
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    if not caminho_audio.exists():
        raise FileNotFoundError(
            f"Download concluído mas arquivo esperado não encontrado: {caminho_audio}"
        )

    return caminho_audio, info


def transcrever_audio(
    caminho_audio: Path,
    modelo: str = "small",
    idioma: str | None = "pt",
    verbose: bool = False,
) -> list[dict]:
    """
    Transcreve o áudio usando faster-whisper.
    Retorna lista de segmentos com start, end e text.

    Progresso: a barra avança conforme o timestamp de fim de cada segmento
    processado, comparado à duração total do áudio (info.duration). Não é
    um "tempo restante" exato — é proporção de áudio já processada — mas é
    a métrica real disponível, já que o whisper não expõe % de conclusão
    diretamente.
    """
    model = WhisperModel(modelo, device="cpu", compute_type="int8")
    print(f"[info] Rodando modelo '{modelo}' em CPU (int8).")

    segmentos_iter, info = model.transcribe(
        str(caminho_audio),
        language=idioma,
        vad_filter=True,  # remove silêncio/ruído, reduz alucinação do modelo
    )

    print(
        f"[info] Idioma detectado: {info.language} "
        f"(confiança: {info.language_probability:.2f})"
    )
    print(f"[info] Duração do áudio: {formatar_timestamp(info.duration)}")

    segmentos = []
    tempo_processado = 0.0
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

    doc.build(story)


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

    print("[0/3] Verificando pastas de destino...")
    audio_dir.mkdir(parents=True, exist_ok=True)
    print(f"[info] Pasta de áudio: {audio_dir.resolve()}")
    if args.output:
        pasta_saida_pdf = Path(args.output).parent
        pasta_saida_pdf.mkdir(parents=True, exist_ok=True)
        print(f"[info] Pasta de saída do PDF: {pasta_saida_pdf.resolve()}")

    print("[1/3] Baixando áudio...")
    caminho_audio, info = baixar_audio(args.url, audio_dir)
    titulo_video = info.get("title", "video")
    print(f"[info] Áudio salvo em: {caminho_audio.resolve()}")

    print("[2/3] Transcrevendo (isso pode demorar dependendo do modelo/hardware)...")
    segmentos = transcrever_audio(
        caminho_audio, modelo=args.model, idioma=idioma, verbose=args.verbose
    )

    if not segmentos:
        print("[erro] Nenhum segmento de fala foi detectado. Abortando.", file=sys.stderr)
        sys.exit(1)

    print("[3/3] Gerando PDF...")
    nome_saida = args.output or f"{titulo_video[:80]}.pdf".replace("/", "-")
    caminho_saida = Path(nome_saida)
    gerar_pdf(
        segmentos,
        titulo=titulo_video,
        url_origem=args.url,
        caminho_saida=caminho_saida,
        incluir_timestamps=not args.no_timestamps,
    )

    print(f"\nConcluído: {caminho_saida.resolve()}")


if __name__ == "__main__":
    main()