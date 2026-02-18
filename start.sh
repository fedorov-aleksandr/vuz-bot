#!/bin/sh
set -e

# Запускает бот в цикле — автоперезапуск при падении
while true; do
  echo "Запуск bot..."
  # Убедиться, что каталог для логов существует
  mkdir -p /app/logs || true
  python main.py &
  PID=$!
  echo $PID > /tmp/bot.pid
  # дождаться завершения процесса
  wait $PID
  EXIT_CODE=$?
  echo "Процесс $PID завершился с кодом $EXIT_CODE, перезапуск через 5 секунд..."
  rm -f /tmp/bot.pid
  sleep 5
done