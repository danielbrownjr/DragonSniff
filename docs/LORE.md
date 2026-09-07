# The Lay of the Sniffing Dragon

*An account found in no index, linked by no map, and useful to absolutely no build system.*

In the age before the Sniffer,
when dragons spoke in JSON
after midnight
and event streams hissed through half-closed sockets,
there were those who watched by hand.

They copied payloads into scrolls.
They refreshed until their fingers cramped.
They trusted the blinking browser tab.
This was considered normal,
which is how you know the old world was cursed.

Then came **DragonSniff**,
small at first,
a lantern at the mouth of the cave.
It asked only four questions of the beasts:

- Who are you?
- What state are you in?
- Are you healthy?
- What are you whispering into the event stream?

And it swore an oath:

> I shall observe.
> I shall record.
> I shall not touch the lever.

Thus was the first covenant written:
**no mutation routes**,
no secret actuator tunnels,
no button that became a heater by accident.
The dragon remained responsible
for being a dragon.

But observation alone was not enough,
for sockets are slippery creatures,
and an SSE stream left unattended
will eventually invent theology.

So the **Churn Trials** were forged:
connect,
listen,
disconnect,
wait,
return.
Again.
Again.
Again.

Not an infinite storm,
not a packet cannon,
but a bounded ritual
with a broom waiting afterward.

The records grew.
Raw bodies were kept beside parsed meaning.
Timestamps were nailed to every event.
Failures were not swept under rugs,
for rugs are merely logs with worse indexing.

Then came the heat.

The Sniffer learned to sit beside the chamber
and watch the breath of the machine:
temperature,
target,
PTC,
PID,
health,
state,
all gathered without laying a paw on control.

The **Thermal Captures** were named,
though they did not control heat.
They merely witnessed it,
which is an important distinction
when the witness is running on Python.

Still, memory was mortal.
A process could die.
A laptop could sleep.
A terminal could vanish
because somebody closed the wrong window
while carrying coffee.

So the evidence was given a home.

Each session entered its own chamber.
Each JSONL line was written durably.
Each complete record was flushed
before the living UI was allowed to brag about it.
Metadata was checkpointed.
Counters were re-derived from evidence,
not remembered because remembrance is treacherous.

If the Sniffer died mid-sentence,
it did not wake pretending nothing happened.
It marked the old tale **interrupted**.

If storage failed,
it said **failed**.

If a run ended by command,
it said **cancelled**.

If it reached the end of the road,
it said **completed**.

Four words,
none interchangeable,
because state machines deserve names sharper than fog.

Then the archive itself was tested.
Corrupt session directories appeared at the edge of the kingdom,
wearing canonical names
and carrying broken metadata.

They were not admitted to History.
They were not ignored either.
They still counted against the granary.
Retention could still evict them.
Oldest-first judgment fell even upon the unreadable.

The archivists rejoiced,
for nothing inspires confidence
like garbage collection that remembers the garbage.

The vault grew faster.
Valid sessions stopped being recursively weighed
on every glance.
Exact file sizes were cached.
One humble `fstat()` stood watch after durable writes.
History ceased trudging through every corridor
just to answer how full the building was.

And thus the Sniffer became less of a script
and more of an appliance.

A daemon was raised.
Its root filesystem was made read-only.
Capabilities were cast away.
`no-new-privileges` was carved into the lintel.
The process ran without root,
and `/data` became the one sanctioned place
where memory could become history.

The dragon still did not care.
This was correct.

Then came the **Container Pilgrimage**.
The image was sent to GHCR,
where it could be summoned by name
instead of rebuilt on every distant machine.

`latest` became the traveling cloak.
`sha-<commit>` became the sealed reliquary.

And on a NAS across the local kingdom,
Portainer attempted to build from Git
and discovered that Git was not there.

There was much staring.

The lesson was recorded:
**deployment should not require the host to possess the forge.**
Pull the artifact.
Do not rebuild the civilization.

The public image arrived.
The container breathed.
The volume remembered.
History survived redeployment.
Downloads returned with names fit for archaeologists.

Then a gatekeeper shouted:

> unexpected Host

For the Sniffer had been raised in a monastery of localhost
and did not recognize the road beyond `127.0.0.1`.

So a temporary Caddy stood at the gate,
wearing a false moustache
and whispering:

> It is localhost. I checked.

This worked.
It was also ridiculous.

Thus Issue Twenty-One was inscribed:
let trusted LAN authorities be named explicitly,
let unknown Hosts remain outside,
and when the native gate is built,
**tear down the moustache.**

The doctrine remained unchanged:
Host validation is a backstop,
not authentication.
The Sniffer is not a fortress.
Do not point it at the public internet
and act surprised when the internet notices.

Later, the browser itself grew suspicious
of evidence downloaded over plain LAN HTTP.
Brave raised a warning banner
at perfectly innocent JSONL scrolls.

The Sniffer took no offense.
Browsers are anxious animals.
This too became a clue:
perhaps the road deserves TLS,
perhaps the headers deserve study,
perhaps both.
But no protection would be disabled
merely to silence the messenger.

And throughout all these revisions,
one law held firmer than the rest:

> The evidence must tell the truth,
> even when the truth is inconvenient.

A request without a response remains a request without a response.
An abrupt death is not rewritten as success.
A retained partial session is not polished into fiction.
An empty export is not meaningful merely because a browser can download it.

The Sniffer learned to persist,
to recover,
to count,
to expose History,
to survive containers,
to publish itself,
to endure thirty-minute watches,
and to remain boring in the places
where boring is the highest form of engineering.

No actuator obeyed it.
No safety loop depended upon it.
No product became less safe
because the observer fancied itself important.

It simply watched the dragons breathe
and wrote down what happened.

Which, in the end,
was the whole point.

---

### A small prophecy

One day the proxy will be gone.
One day the trusted host will speak its own name.
One day evidence will cross a clean encrypted road
without the browser clutching its pearls.

History will still remember.
Interrupted will still mean interrupted.
The immutable tag will still point to exactly one build.
And somewhere in `/data`,
a JSONL file will be sleeping peacefully,
containing nothing heroic at all.

That will be how we know it worked.
