#!/usr/bin/env bash
#
# transcrever.sh
#
# Roda youtube_transcribe_pdf.py em segundo plano, imune a fechamento do
# terminal ou queda de sessão SSH — pode fechar o terminal logo depois de
# rodar este script sem matar o processo.
#
# Uso (mesmos parâmetros do script Python, repassados direto):
#   ./transcrever.sh "https://youtube.com/watch?v=XXXX"
#   ./transcrever.sh "URL" --model medium --lang pt --output saida.pdf
#
# Acompanhar depois:
#   tail -f transcricao.log            # log estruturado (progresso, erros)
#   ps -p <PID>                        # confirmar se ainda está rodando
#
set -euo pipefail

DIR_RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR_RAIZ"

if [ "$#" -eq 0 ]; then
    echo "Uso: $0 \"URL_DO_VIDEO\" [--model medium] [--lang pt] [--output saida.pdf] ..."
    exit 1
fi

# Ativa o ambiente virtual, aceitando os dois nomes comuns (.venv ou venv),
# já que as duas máquinas usadas até agora usam nomes diferentes.
if [ -f "$DIR_RAIZ/.venv/bin/activate" ]; then
    source "$DIR_RAIZ/.venv/bin/activate"
elif [ -f "$DIR_RAIZ/venv/bin/activate" ]; then
    source "$DIR_RAIZ/venv/bin/activate"
else
    echo "[erro] Ambiente virtual não encontrado (.venv/ ou venv/ em $DIR_RAIZ)." >&2
    exit 1
fi

SAIDA_BRUTA="$DIR_RAIZ/transcricao_stdout.log"

# nohup: ignora SIGHUP (sobrevive ao fechamento do terminal/SSH mesmo se
#        huponexit estiver ativado no shell, diferente do que vimos antes).
# disown: remove da tabela de jobs desta shell, então nem aparece mais
#        aqui pra ser afetado por nada que essa sessão fizer depois.
# stdout/stderr redirecionados para arquivo à parte do log estruturado do
# Python — cobre erros que acontecem ANTES do logging ser configurado
# (ex.: falta de dependência, venv quebrado) e a barra de progresso do
# tqdm, que não vai para o transcricao.log.
nohup python "$DIR_RAIZ/youtube_transcribe_pdf.py" "$@" > "$SAIDA_BRUTA" 2>&1 &
PID=$!
disown

echo "Iniciado em segundo plano. PID: $PID"
echo "Pode fechar o terminal agora."
echo ""
echo "Log estruturado (progresso, erros com traceback): $DIR_RAIZ/transcricao.log"
echo "Saída bruta (stdout/stderr, inclui erros de inicialização): $SAIDA_BRUTA"
echo "Checar se ainda está rodando:  ps -p $PID"
echo "Acompanhar ao vivo:            tail -f $DIR_RAIZ/transcricao.log"