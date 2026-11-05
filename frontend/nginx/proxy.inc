# Общее для всего, что уходит на бэкенд: /api/, админка, /static/.
# Раньше у /static/ не было ни Host, ни X-Forwarded-*: Django видел имя
# сервиса вместо домена.
proxy_pass         $backend;
proxy_http_version 1.1;
proxy_set_header   Host              $host;
proxy_set_header   X-Forwarded-Host  $host;
proxy_set_header   X-Real-IP         $remote_addr;
# Один адрес — клиента, уже проверенный realip; не цепочка от клиента.
proxy_set_header   X-Forwarded-For   $remote_addr;
proxy_set_header   X-Forwarded-Proto $forwarded_proto;
proxy_read_timeout 60s;
# Эти заголовки ставит и Django; два одинаковых X-Frame-Options браузер
# может счесть ошибочными и проигнорировать. Источник один — nginx.
proxy_hide_header  X-Frame-Options;
proxy_hide_header  X-Content-Type-Options;
proxy_hide_header  Referrer-Policy;
proxy_hide_header  Strict-Transport-Security;
