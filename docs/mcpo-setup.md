# MCPO setup

1) Start the MCP server
- make run-mcp
- or, after installation: icinga-mcp-server
- or: scripts/run-mcp.sh

2) Configure MCPO to spawn the local MCP server. Example MCPO server entry:

{
  "name": "icinga-mcp",
  "command": "bash",
  "args": ["-lc", ". .venv/bin/activate && icinga-mcp-server"],
  "env": {
    "ICINGA_WEB_BASE_URL": "https://monitoring.example.com/icingaweb2",
    "ICINGA_WEB_USERNAME": "api-user",
    "ICINGA_WEB_PASSWORD": "change-me",
    "ICINGA_WEB_VERIFY_TLS": "true",
    "ICINGA_WEB_CA_BUNDLE": "/path/to/ca.pem"
  }
}

3) Host and service listings with summaries
- All list_* tools accept optional filter, page, limit.
- To reduce tokens sent to the LLM, summary is enabled by default and you can override the projected fields.

Controls
- summary: true|false (default: true). When true, returns a compact subset of fields.
- fields: comma-separated list of dotted field paths to include (overrides the default summary set).

Default summary fields
- Hosts: name, state.code, state.name, state.is_problem, last_check, last_state_change, acknowledged, in_downtime, output
- Services: host.name, name, state.code, state.name, state.is_problem, last_check, last_state_change, acknowledged, in_downtime, output
- Events (history): time, type, state.code, state.name, author, text, output

Examples (MCPO tools)
- icinga-mcp.list_hosts()                                     # summarized host list
- icinga-mcp.list_hosts(fields="name,state.code,output")       # explicit summary fields
- icinga-mcp.list_hosts(summary=false)                         # full upstream host objects

- icinga-mcp.list_services()                                   # summarized service list
- icinga-mcp.list_services(filter="host.name==web01")          # dotted filters
- icinga-mcp.list_services(summary=false)                      # full upstream service objects

4) History with optional time windows
- list_host_history(name=...), list_service_history(name=..., host=...) support timerange to keep payloads lean.
- timerange: hour | day | week | month | quarter | year | all (default: all).
- The window is applied locally by the MCP server based on event timestamps.

Examples
- icinga-mcp.list_host_history(name="deluxe.vm.icinga.com", timerange="day")
- icinga-mcp.list_service_history(name="Load", host="urgent.vm.icinga.com", fields="time,type,output", timerange="week")

5) Problem listings with summaries
- Same summary and fields controls as above.

Examples
- icinga-mcp.list_host_problems()                                  # summarized host problems
- icinga-mcp.list_host_problems(host="arnold.vm.icinga.com")       # summarized, filtered to a host
- icinga-mcp.list_host_problems(fields="name,state.code,output")   # explicit summary fields
- icinga-mcp.list_host_problems(summary=false)                     # full upstream host objects

- icinga-mcp.list_service_problems()                               # summarized service problems
- icinga-mcp.list_service_problems(host="arnold.vm.icinga.com", service="Load")  # summarized, filtered
- icinga-mcp.list_service_problems(fields="host.name,name,state.code,output")    # explicit fields
- icinga-mcp.list_service_problems(summary=false)                  # full upstream service objects

6) Detail lookups
- Return full objects by default; use summary=true or fields="a,b,c" to reduce payload.

Examples
- icinga-mcp.get_host_detail(name="web01")
- icinga-mcp.get_host_detail(name="web01", summary=true, fields="name,state.code,output")
- icinga-mcp.get_service_detail(name="HTTP", host="web01")

7) Groups
- List host and service groups. When name is provided, returns the exact group via the upstream detail endpoint.

Examples
- icinga-mcp.list_hostgroups()
- icinga-mcp.list_hostgroups(name="linux-servers")
- icinga-mcp.list_servicegroups()
- icinga-mcp.list_servicegroups(name="http-checks")

8) Downtimes and comments listings
- Pass-through lists with filter, page, limit.

Examples
- icinga-mcp.list_downtimes(filter="host.name==web01")
- icinga-mcp.list_comments(filter="host.name==web01 && service.name==HTTP")

9) Comment tools
Create comments against host or service objects using the upstream Icinga DB Web endpoints.

Examples
- icinga-mcp.add_host_comment(name="thermos.vm.icinga.com", comment="Planned maintenance", expire="n")
- icinga-mcp.add_service_comment(name="Load", host="thermos.vm.icinga.com", comment="Noted by on-call", expire="y")

Notes
- expire accepts 'y' or 'n' (strings) for yes/no.
- These map to upstream Icinga DB Web:
  - POST /icingadb/host/add-comment?name=host
  - POST /icingadb/service/add-comment?name=service&host.name=host
