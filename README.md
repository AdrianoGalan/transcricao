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

**Direto (processo preso ao terminal):**
```bash
python youtube_transcribe_pdf.py "https://youtube.com/watch?v=XXXX"
```

**Em segundo plano (sobrevive a fechar o terminal / queda de SSH):**
```bash
chmod +x transcrever.sh   # só na primeira vez
./transcrever.sh "https://youtube.com/watch?v=XXXX"
```
`transcrever.sh` repassa todos os parâmetros direto pro script Python, então
qualquer opção da tabela abaixo funciona igual em ambas as formas:
```bash
./transcrever.sh "URL" --model medium --lang pt --output saida.pdf
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
de forma limpa — nada fica rodando em segundo plano sem você saber.

## Log

Tudo é registrado em `transcricao.log`, na raiz do projeto, com timestamp e
nível — além de aparecer no terminal enquanto a sessão estiver conectada.
Qualquer falha (download, transcrição, geração do PDF, ou erro não previsto)
é gravada com traceback completo, então mesmo rodando em segundo plano sem
ninguém olhando dá pra saber depois exatamente o que aconteceu e em qual
etapa.

Ao rodar via `./transcrever.sh`, existe um segundo arquivo,
`transcricao_stdout.log`, que captura a saída bruta do processo (stdout/
stderr) — cobre erros que acontecem antes do logging ser inicializado, como
dependência faltando ou ambiente virtual quebrado.

```bash
tail -f transcricao.log            # acompanhar progresso/erros ao vivo
ps -p <PID>                        # confirmar se o processo ainda está rodando
```

**Limite conhecido**: se o processo for morto pelo OOM killer do kernel (falta
de memória RAM), nem o log estruturado nem o stdout capturam nada — o
processo não tem chance de escrever antes de morrer. Nesse caso, a evidência
fica nos logs do sistema:
```bash
dmesg -T | grep -i "killed process"
journalctl -k | grep -i "out of memory"
```

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
├── transcrever.sh          # roda o script acima em segundo plano
├── transcricao.log         # log estruturado (não versionado)
├── transcricao_stdout.log  # saída bruta ao rodar via transcrever.sh (não versionado)
├── audios/                 # áudios baixados (não versionado)
├── .venv/                  # ambiente virtual (não versionado)
└── *.pdf                   # transcrições geradas (não versionado)
```

## Limitações conhecidas

- Não faz diarização (não identifica quem fala em vídeos com múltiplos falantes).
- Depende de disponibilidade de rede e da estabilidade do `yt-dlp` frente a
  mudanças do YouTube — extrações podem quebrar sem aviso.
- Sem checkpoint de progresso: se o processo for interrompido no meio da
  transcrição, o trabalho feito até ali é perdido (o áudio baixado, não).
- Log em arquivo não protege contra `SIGKILL` externo (OOM killer) — ver
  seção "Log" acima.

## Licença de uso

Baixar e transcrever vídeos de terceiros para uso pessoal é uma coisa;
redistribuir a transcrição de conteúdo protegido é outra. O uso é de
responsabilidade de quem roda o script.