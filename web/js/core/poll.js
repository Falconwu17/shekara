// Периодический опрос, который страница останавливает при уходе с неё.

/** every(2000, load): вызывает load сразу и затем каждые ms; возвращает функцию остановки. */
export function every(ms, callback) {
  let stopped = false;
  const run = async () => {
    if (!stopped) await callback();
  };
  run();
  const timer = setInterval(run, ms);
  return () => {
    stopped = true;
    clearInterval(timer);
  };
}
