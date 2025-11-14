# Icinga DB Web API notes

This project targets the Icinga Web 2 Icinga DB Web module. The exact endpoints and filter grammar may vary by version and deployment path.

Default mapping (relative to icingaweb2 root):
- /icingadb/hosts
- /icingadb/services
- /icingadb/notifications
- /icingadb/downtimes
- /icingadb/comments
- /icingadb/hostgroups          # overview (list)
- /icingadb/servicegroups       # overview (list)
- /icingadb/hostgroup           # detail (requires ?name=...)
- /icingadb/servicegroup        # detail (requires ?name=...)
- /icingadb/host/history
- /icingadb/service/history


Endpoints are fixed to the Icinga DB Web defaults under /icingadb; environment overrides have been removed.
 
## REST authentication (Bearer)
The REST server uses global Bearer token authentication. Configure in your .env:

- REST_BEARER_TOKEN=your-secret to set the expected token
- REST_REQUIRE_API_KEY=true to enforce authentication globally (if enabled without a token, requests will return 500 to indicate misconfiguration)

Behavior:
- When enabled, all API endpoints require:
  - Authorization: Bearer your-secret
- Swagger/OpenAPI:
  - /docs and /openapi.json do not require auth to load, so you can click "Authorize" in Swagger UI and enter your Bearer token to test secured endpoints.
- Misconfiguration guard:
  - If REST_REQUIRE_API_KEY=true but REST_BEARER_TOKEN is not set, the server responds with HTTP 500.

Examples:
- .env:
  - REST_REQUIRE_API_KEY=true
  - REST_BEARER_TOKEN=super-secret
- curl (Bearer):
  - curl -H "Authorization: Bearer super-secret" http://127.0.0.1:8080/health

Filtering and pagination (REST):
- Do not use the "filter" query param on REST routes. Instead, pass precise dotted filters like host.name, service.name, host.state.is_problem, etc. These are forwarded as-is to Icinga DB Web.
- Pagination: "page" (1-based), "limit" (page size).
- Convenience params: ?host=... maps to host.name, ?service=... maps to service.name.
  - On /hosts and /problems/hosts only ?host is applied; ?service is not supported.
  - On /services and /problems/services both ?host and ?service are supported.
- History endpoints:
  - Upstream host history: GET /icingadb/host/history with required name=<host>
  - Upstream service history: GET /icingadb/service/history with required name=<service> and host.name=<host>
  - REST routes:
    - GET /history/host requires host=<host> (also accepts name=<host>); it does not accept service, service.name or any service.* params.
    - GET /history/service supports service=<service>&host=<host> (also accepts name=<service>&host.name=<host>)
    - Both history routes support an additional timerange parameter to limit returned events:
      - timerange=hour | day | week | month | quarter | year | all (default: all)
      - Semantics: the server fetches upstream history and filters locally to entries with event_time (or fallback time) greater than or equal to now minus the selected window.
        - hour: 1 hour
        - day: 1 day
        - week: 7 days
        - month: 30 days
        - quarter: 90 days
        - year: 365 days
        - all: no time filtering
      - Notes:
        - Times are evaluated in UTC.
        - Filtering is applied after retrieving data from Icinga DB Web to reduce payload for clients/LLMs.
- Groups endpoints:
  - Upstream overview:
    - GET /icingadb/hostgroups
    - GET /icingadb/servicegroups
  - Upstream details (single group view):
    - GET /icingadb/hostgroup?name=<group>
    - GET /icingadb/servicegroup?name=<group>
  - REST routes:
    - GET /hostgroups and GET /servicegroups
    - When you include name=<group>, the upstream call uses the singular detail endpoint automatically. Host/service dotted filters or generic filter are not supported on these routes.
- Problems endpoints (REST):
  - REST routes: GET /problems/hosts and GET /problems/services
  - /problems/hosts lists hosts with problems (enforces host.state.is_problem = y by default). Supports page and limit, the convenience ?host=... (mapped to host.name), and only host.* dotted filters.
  - /problems/services lists services with problems (enforces service.state.is_problem = y by default). Supports page and limit, the convenience ?host=... and ?service=... (mapped to host.name and service.name), and only service.* dotted filters.

Please verify against your Icinga DB Web /docs. If your upstream path differs, adjust your reverse proxy or update the code's fixed mappings.

## Summary projection and "fields"
- Many REST list endpoints support compact summaries by default via the summary query flag (default: true). When enabled, responses are normalized and flattened for UI consumption (for example, exposing state.code/state.name, acknowledged, in_downtime, output).
- Use fields to control which dotted fields are returned in summary mode. Provide a comma-separated list, e.g., fields=host.name,state.code,state.name,acknowledged,output.
- Set summary=false to receive full upstream records without projection.
- Endpoints supporting summary/fields: GET /hosts, /services, /problems/hosts, /problems/services, /comments (summary shows safe compact shape), /downtimes, /notifications, group member listings when name is provided (GET /hostgroups?name=..., GET /servicegroups?name=...), and history endpoints (/history/host, /history/service) where summary provides flattened event data.

References:
- Routes and schemas: [src/icinga_mcp/rest_app.py](src/icinga_mcp/rest_app.py:1)
- Service calls and upstream mapping: [src/icinga_mcp/services.py](src/icinga_mcp/services.py:1)
- Example requests: [examples/http-examples.http](examples/http-examples.http:1)

## Downtimes

- Listing:
  - Upstream: GET /icingadb/downtimes
  - REST route: GET /downtimes
    - Supports dotted filters like host.name, service.name, etc.

- Scheduling:
  - Host downtime
    - Upstream: POST /icingadb/host/schedule-downtime?name=<host>
    - REST: POST /downtime/host?host=<host> (also accepts name=<host&gt>)
      - Body (JSON):
        {
          "comment": "nix",
          "window": "hour" | "day" | "week"
        }
      - The REST server computes start and end timestamps based on window:
        - hour: now to now+1h
        - day: now to now+24h
        - week: now to now+7d
      - Time format sent to upstream: YYYY-MM-DDTHH:MM:SS (naive local time).
      - Fixed flags (not configurable via REST; enforced when calling upstream):
        - flexible = n
        - all_services = n
        - child_options = 0
      - Implementation:
        - Route: [app.post() -> post_downtime_host](src/icinga_mcp/rest_app.py:596)
        - Service call: [IcingaDBService.schedule_downtime_host()](src/icinga_mcp/services.py:248)

  - Service downtime
    - Upstream: POST /icingadb/service/schedule-downtime?name=<service>&host.name=<host>
    - REST: POST /downtime/service?service=<service>&host=<host> (also accepts name=<service>&host.name=<host>)
      - Body (JSON):
        {
          "comment": "hurra",
          "window": "hour" | "day" | "week"
        }
      - The REST server computes start and end timestamps based on window (same as host downtime).
      - Time format sent to upstream: YYYY-MM-DDTHH:MM:SS (naive local time).
      - Fixed flags (not configurable via REST; enforced when calling upstream):
        - flexible = n
        - child_options = 0
      - Implementation:
        - Route: [app.post() -> post_downtime_service](src/icinga_mcp/rest_app.py:628)
        - Service call: [IcingaDBService.schedule_downtime_service()](src/icinga_mcp/services.py:268)

- Removing:
  - Upstream: POST /icingadb/downtimes/delete?downtime.name=<name>
  - REST: POST /downtime/remove
    - Body (JSON):
      {
        "name": "sardine.vm.icinga.com!f09a0d5a-52f3-4361-a550-2aeecb1d0a3d"
      }
    - Notes:
      - The 'name' value must be taken verbatim from GET /downtimes for the specific downtime to delete. Do not send id.
      - Downtime name formats:
        - Host downtime: <host>!<uuid>
        - Service downtime: <host>!<service>!<uuid>
  - Implementation:
    - Route: [post_remove_downtime()](src/icinga_mcp/rest_app.py:657)
    - Service call: [IcingaDBService.remove_downtime()](src/icinga_mcp/services.py:174)

Notes:
- These REST routes mirror the host/service-specific endpoints in Icinga DB Web rather than the generic actions API, to align with the upstream URLs you provided.
- If your deployment differs, adjust your reverse proxy or update the fixed endpoint mapping in code.

## Comments

- Listing:
  - Upstream: GET /icingadb/comments
  - REST route: GET /comments
    - Supports scoping via host and service query parameters (equal comparisons to host.name and service.name).

- Adding:
  - Host comment
    - Upstream: POST /icingadb/host/add-comment?name=<host>
    - REST: POST /comment/host?host=<host> (also accepts name=<host> for backward compatibility)
      - Body schema (JSON):
        {
          "comment": "Planned maintenance"
        }
      - Notes:
        - The REST body contains only 'comment'. The REST server does not accept an 'expire' switch; it always sends expire='n' to Icinga DB Web.
        - The REST handler forwards upstream using key-value form fields (application/x-www-form-urlencoded): comment=<text>, expire=n.
        - The host name must be provided as a query parameter via 'host' (preferred) or legacy 'name'.
  - Service comment
    - Upstream: POST /icingadb/service/add-comment?name=service&host.name=host
    - REST: POST /comment/service?service=service&host=host (also accepts name=service&host.name=host for backward compatibility)
      - Body schema (JSON):
        {
          "comment": "Investigating"
        }
      - Notes:
        - The REST body contains only 'comment'. The REST server does not accept an 'expire' switch; it always sends expire='n' to Icinga DB Web.
        - The REST handler forwards upstream using key-value form fields (application/x-www-form-urlencoded): comment=<text>, expire=n, with query params name=<service> and host.name=<host>.
        - The service and host must be provided as query parameters (?service=... & ?host=...); also accepts (?name=... & host.name=...) for backward compatibility.

- Deleting:
  - Upstream expects the composite comment name: POST /icingadb/comments/delete?comment.name=<name>
  - REST route: POST /comment/remove
    - Request body (required):
      {
        "name": "answer.vm.icinga.com!f1cd84f1-583e-493f-b42b-86b9e1c6d325"
      }
    - The name value must be taken verbatim from GET /comments for the specific comment to delete. Do not send id. The Web UI must submit name only and must not include an id parameter.
    - Comment name formats:
      - Host comment: <host>!<uuid> (e.g., answer.vm.icinga.com!f1cd84f1-583e-493f-b42b-86b9e1c6d325)
      - Service comment: <host>!<service>!<uuid>

- References:
  - Route definitions:
    - [src/icinga_mcp/rest_app.py](src/icinga_mcp/rest_app.py)
    - [src/icinga_mcp/services.py](src/icinga_mcp/services.py)
  - Example requests: [examples/http-examples.http](examples/http-examples.http)

## Notifications

- Listing:
  - Upstream: GET /icingadb/notifications
  - REST route: GET /notifications
    - Supports dotted filters like host.name and service.name. Convenience parameters host and service are mapped to dotted filters.
    - Summary mode is enabled by default; control output via the fields query parameter.
    - Default summary fields (override with NOTIFICATION_SUMMARY_FIELDS): time, host, service, author, text
  - Timerange:
    - Query parameter: timerange=hour | day | week | month | quarter | year | all (default: all)
    - Semantics: the server fetches upstream notifications and filters locally to entries whose timestamp is within the selected window.
      - Timestamp sources (first available): entry_time, time, event_time
      - Window definitions (UTC):
        - hour: 1 hour
        - day: 1 day
        - week: 7 days
        - month: 30 days
        - quarter: 90 days
        - year: 365 days
        - all: no time filtering
  - Target upstream base URL example:
    - ICINGA_WEB_BASE_URL=https://icinga-server/icingaweb2

- Examples:
  - GET /notifications?host=web01&timerange=day
  - GET /notifications?service=HTTP&host=web01&timerange=week&fields=time,host,service,author,text

- References:
  - Routes: [src/icinga_mcp/rest_app.py](src/icinga_mcp/rest_app.py)
  - Service calls: [src/icinga_mcp/services.py](src/icinga_mcp/services.py)
  - Examples: [examples/http-examples.http](examples/http-examples.http)

## Acknowledgements

- Listing:
  - Upstream: GET /icingadb/acknowledgements
  - REST route: removed
  - Notes: The REST listing endpoint has been removed. If you need acknowledgement entries, query upstream Icinga DB Web directly or use problem listings and object history.

- Adding:
  - Host acknowledgement
    - Upstream: POST /icingadb/host/acknowledge?name=<host>
    - REST: POST /acknowledgement/host?host=<host> (also accepts name=<host>)
    - Body (JSON):
      {
        "comment": "ich bin dran"
      }
    - Fixed flags (not configurable via REST; enforced by the server when calling upstream):
      - persistent = n
      - notify = y
      - sticky = n
      - expire = n
    - Validation/Limitations:
      - Only comment is accepted from the client; other acknowledgement flags are fixed as above.
      - Service parameters (service, service.name, any service.*) are rejected on this endpoint.
    - Implementation references:
      - REST route: [src/icinga_mcp/rest_app.py](src/icinga_mcp/rest_app.py)
      - Service call: [src/icinga_mcp/services.py](src/icinga_mcp/services.py)

  - Service acknowledgement
    - Upstream: POST /icingadb/service/acknowledge?name=<service>&host.name=<host>
    - REST: POST /acknowledgement/service?service=<service>&host=<host> (also accepts name=<service>&host.name=<host>)
    - Body (JSON):
      {
        "comment": "ich bin dran"
      }
    - Fixed flags (not configurable via REST; enforced by the server when calling upstream):
      - persistent = n
      - notify = y
      - sticky = n
      - expire = n
    - Validation/Limitations:
      - Requires both service and host; or legacy name and host.name.
      - Only comment is accepted from the client; other acknowledgement flags are fixed as above.
    - Implementation references:
      - REST route: [src/icinga_mcp/rest_app.py](src/icinga_mcp/rest_app.py)
      - Service call: [src/icinga_mcp/services.py](src/icinga_mcp/services.py)

- Removing:
  - Host acknowledgement removal
    - Upstream: POST /icingadb/host/remove-acknowledgement?name=<host>
    - REST: POST /acknowledgement/host/remove?host=<host> (also accepts name=<host>)
  - Service acknowledgement removal
    - Upstream: POST /icingadb/service/remove-acknowledgement?name=<service>&host.name=<host>
    - REST: POST /acknowledgement/service/remove?service=<service>&host=<host> (also accepts name=<service>&host.name=<host>)
  - Implementation references:
    - REST routes: [src/icinga_mcp/rest_app.py](src/icinga_mcp/rest_app.py)
    - Service calls: [src/icinga_mcp/services.py](src/icinga_mcp/services.py)

- Notes for UI implementers:
  - These acknowledgement routes mirror the reworked comment routes: object identity is provided via query params, while the request body is minimal and only carries comment text.
  - The fixed flags are chosen to match the requested behavior. If your upstream policy differs, adjust server defaults in code.

- Examples:
  - See: [examples/http-examples.http](examples/http-examples.http)
