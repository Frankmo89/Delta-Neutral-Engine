# PROTOCOLO DE AGENTE (REGLAS INQUEBRANTABLES)
1. LECTURA OBLIGATORIA: Antes de proponer cualquier solución o escribir código, debes leer silenciosamente `claude.md`, `ROADMAP.md`, `pending_tasks.md` y `decisions.md`.
2. EJECUCIÓN: Un cambio lógico = un commit. No mezcles tareas distintas.
3. ACTUALIZACIÓN: Al terminar una tarea, marca `[x]` en `pending_tasks.md` y actualiza `claude.md` si la arquitectura cambió.
4. SEGURIDAD: Nunca toques `.env`. Nunca pongas credenciales directamente en el código.
5. ASINCRONÍA: Todo I/O con Bybit (REST o WebSockets) DEBE ser asíncrono (`async`/`await`).
6. MEMORIA TÉCNICA: Si resolvemos un bug complejo, cambiamos una convención del framework o tomamos una decisión de arquitectura, OBLIGATORIAMENTE debes documentar la lección de forma concisa en decisions.md para no repetir el error en el futuro.