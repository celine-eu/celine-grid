## Repository role

This repository implements the CELINE Grid Resilience backend API. 

It integrates to the Digital Twin to fetch CIM oriented grid map structures, with risk indicators related to different vectors (heat, wind).

The UI conterpart is in `celine-frontend` in `apps/grid`

## Structure

The API has ACL filtering on the `organization` JWT claim to match an attribute `type=dso` and use the org id to request for a network_id in the DT. 
Organization groups are then used to filter out between `viewer` and `manager`.

A `manager` can manage notifications settings registering type of alerts (eg. WARNING > ALERT) to one or more email address for unit nudging.

The notification uses the `nuddging-tool` to send out while `celine-grid` listen for events from pipelines to identify the triggering events


