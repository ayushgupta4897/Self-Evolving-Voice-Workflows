# Submission copy

## One-line description

**Our voice agent catches itself giving a confidently wrong answer, works out which node
in its own conversation graph is responsible, and rewrites that node — with no human in
the loop.**

*Alternates, same idea, different emphasis:*
- A voice agent that rewrites its own conversation graph when customers get wrong answers.
- Voice agents fail by being fluent and wrong. Ours notices, finds the cause, and patches itself.

---

## Project description (~570 words)

Ask a voice agent what a brake job costs and it will tell you. Confidently. In a warm,
competent voice. And sometimes the number is invented.

We asked ours what front brakes cost on a 2021 Toyota Highlander. The verified answer is
**$340**. It said *"right around 550 to 750 dollars."* Fluent, plausible, wrong — and
completely undetectable by ear. That is the real blocker to putting voice agents in front
of customers. Not latency. Not interruption handling. The fact that a wrong answer sounds
exactly like a right one.

Today the fix loop is entirely human: a customer complains, an engineer reads the
transcript, patches a prompt, redeploys. The organisation learns. **The agent doesn't.**

We closed that loop. Every call is scored against a verified knowledge base, which returns
a grounded answer and a verbatim citation. When a turn comes back ungrounded, a background
worker fires with zero human clicks: it identifies which node in the workflow graph is
actually responsible — the root node, not merely the one that spoke — then generates
**three** candidate rewrites using three structurally different mutation operators. Each
candidate is replayed against every historical call that used to pass. A candidate is
promoted only if it fixes the new failure **and** breaks nothing old. The survivor is
written back to the live graph and published. The next call runs the evolved workflow.

In one batch run: **11 generations, 33 candidates, 27 killed by the validator, 3 promoted,
and 10 of 11 generations eliminated something.** That last number is the one we care about
— it is the difference between selection and a pipeline that applies one patch at a time.
The dead candidates are kept and shown, because a gate that never rejects anything isn't a
gate.

Here is the part that surprised us. One promoted patch said, in effect: *never state a
price you have not retrieved this turn.* We then pointed a **healthcare** agent at an
insurance-copay question in a domain sharing no vocabulary with car servicing. It
fabricated a $40 copay. Its failure signature — failure type, node role, a tool that was
available and not invoked — matched the brake-pricing failure **exactly**, because the
signature deliberately encodes structure and no domain words at all. The auto-servicing
patch came back at cosine 1.000 with healthcare excluded from the search. Applied verbatim,
the healthcare agent retrieved and answered **$47**, correctly. A rule learned about brake
prices fixed a medical billing question. Lexical overlap between the two knowledge bases is
0.18, and every shared word is boilerplate.

The tools weren't chosen for a logo slot; each answered a problem we actually hit. To evolve,
the agent had to know when it was verifiably wrong — **Senso** supplies the verified knowledge,
the grounded answer and the citation, and it also holds the escalation policy, so editing the
knowledge base changes when the agent may transfer a caller with zero code change. Adaptive
inference only means something if the failures are yours, so **Pioneer** serves every call the
agent makes. A patch is only worth keeping if it can find the next failure that looks like it,
so **Actian** stores survivors keyed on that signature-only embedding. You cannot evolve what
you cannot read, write and version — **Dograh** exposes the workflow graph over REST, and
gen-0 through gen-4 coexist as real versions. And the validator that decides what ships is
published on **Guild** as a versioned, auditable agent; on one real candidate it independently
returned *reject* on a different model family and named the persona that would break.

We wrote the fitness function and the four mutation operators. It cannot invent a new node
type — that boundary is real. Inside it, we did not write the rules it found.
