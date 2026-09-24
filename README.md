# youtube-transcribe-pdf

Baixa o áudio de um vídeo do YouTube, transcreve localmente com
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) e gera um PDF
com a transcrição (com ou sem marcações de tempo).

Roda 100% local — nenhum áudio ou texto sai da sua máquina. O custo disso é
tempo de processamento em CPU (ver seção de performance abaixo).

## Requisitos

- Python 3.10+
- `ffmpeg` instalado no sistema:
  ```bash
  # Ubuntu/Debian
  sudo apt install ffmpeg
  ```
  (macOS: `brew install ffmpeg` · Windows: baixar de ffmpeg.org e colocar no PATH)

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate
pip install yt-dlp faster-whisper reportlab tqdm psutil
```

## Uso

```bash
python youtube_transcribe_pdf.py "https://youtube.com/watch?v=XXXX"
```

Com opções:

```bash
python youtube_transcribe_pdf.py "URL" \
  --model medium \
  --lang pt \
  --output saida.pdf \
  --audio-dir audios \
  --verbose
```

| Opção             | Padrão    | Descrição                                                        |
|-------------------|-----------|-------------------------------------------------------------------|
| `--model`         | `small`   | Modelo Whisper: `tiny`, `base`, `small`, `medium`, `large-v3`     |
| `--lang`          | `pt`      | Código do idioma, ou `auto` para detecção automática              |
| `--output`        | (título do vídeo) | Caminho do PDF de saída                                   |
| `--audio-dir`     | `audios`  | Pasta onde o áudio baixado é salvo/reutilizado                   |
| `--no-timestamps` | desligado | Remove as marcações de tempo do PDF                               |
| `--verbose`       | desligado | Imprime o texto de cada segmento durante a transcrição            |

## Como funciona

1. **Verifica as pastas** de destino (áudio e, se `--output` tiver subpasta, o PDF) e cria o que faltar.
2. **Baixa o áudio** com `yt-dlp` em mp3. Se o arquivo já existir em `audios/` (execução anterior), pula o download e reaproveita.
3. **Transcreve** com `faster-whisper` em CPU (`int8`), com barra de progresso baseada na duração do áudio já processada.
4. **Gera o PDF** com `reportlab`, um parágrafo por segmento de fala.

Ctrl+C durante qualquer etapa mata o processo e todos os filhos (ex.: `ffmpeg`)
de forma limpa — nada fica rodando em segundo plano.

## Performance (sem GPU)

Referência de tempo de transcrição por hora de áudio, em CPU:

| Modelo    | Qualidade         | Tempo aproximado por hora de áudio |
|-----------|-------------------|-------------------------------------|
| tiny/base | baixa, boa pra triagem | rápido                        |
| small     | equilíbrio razoável    | moderado                      |
| medium    | boa                    | 5-10x mais lento que small    |
| large-v3  | melhor disponível      | impraticável em CPU pra vídeos longos |

Com GPU NVIDIA (CUDA), `medium` e `large-v3` ficam viáveis mesmo para vídeos
de 1h+. Este script não detecta GPU automaticamente — está fixo em CPU
(veja `transcrever_audio()` no código se quiser adaptar).

## Estrutura do projeto

```
.
├── youtube_transcribe_pdf.py
├── audios/          # áudios baixados (não versionado)
├── .venv/           # ambiente virtual (não versionado)
└── *.pdf            # transcrições geradas (não versionado)
```

## Limitações conhecidas

- Não faz diarização (não identifica quem fala em vídeos com múltiplos falantes).
- Depende de disponibilidade de rede e da estabilidade do `yt-dlp` frente a
  mudanças do YouTube — extrações podem quebrar sem aviso.
- Sem checkpoint de progresso: se o processo for interrompido no meio da
  transcrição, o trabalho feito até ali é perdido (o áudio baixado, não).

## Licença de uso

Baixar e transcrever vídeos de terceiros para uso pessoal é uma coisa;
redistribuir a transcrição de conteúdo protegido é outra. O uso é de
responsabilidade de quem roda o script.
