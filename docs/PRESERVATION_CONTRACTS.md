# Typed Preservation Contracts V1

This increment makes **preservation itself a first-class typed object** beneath
proposal/search machinery.

It is stacked on the finite representation-preservation and Board morphism
binding work. It does not add another solver, scheduler, agent runtime, truth
store or state-admission path.

The central separation is:

~~~text
canonical state
    ↓
declared scientific morphism
    ↓
typed preservation contract
    ↓
proposal / search / solver / AI
    ↓
candidate
    ↓
typed verification receipt
    ↓
admission-eligibility gate
    ↓
separate state admission authority
~~~

The preservation layer can say that a candidate is **eligible** under a declared
contract and loss policy. It never performs admission.

## 1. Contract identity

ciw.preservation-contract.v1 binds to:

- one exact ciw.morphism-registry.v1 digest;
- one exact ciw.scientific-morphism.v1 identity and digest;
- that morphism's exact domain representation;
- that morphism's exact codomain representation.

A changed scientific registry or morphism requires a new preservation contract.

This layer deliberately does **not** upgrade the scientific morphism's existing
textual preservation statements into automatically proven laws. The typed
contract is an additional declaration requiring its own verification.

## 2. Property effects

A contract contains explicit input requirements plus one effect for every
property it chooses to classify.

Unmentioned properties are **UNKNOWN**.

The V1 effects are:

| Effect | Meaning |
| --- | --- |
| PRESERVE | The same named property is claimed available after the morphism. |
| TRANSFORM | A named input property becomes a different named output property through a declared transformation rule. |
| BOUND | The same property remains available subject to a declared quantitative error bound. |
| FORGET | The property is explicitly not available after the morphism. |

Example:

~~~json
{
  "contract_id": "thermal.reduction-contract.v1",
  "morphism_id": "thermal.reduced-order-map.v1",
  "requires": [
    "calibration.temperature.v1"
  ],
  "effects": [
    {
      "property_id": "physical.mass.v1",
      "effect": "PRESERVE",
      "output_property_id": "physical.mass.v1",
      "transform_id": null,
      "bound": null,
      "notes": ""
    },
    {
      "property_id": "field.temperature.v1",
      "effect": "BOUND",
      "output_property_id": "field.temperature.v1",
      "transform_id": null,
      "bound": {
        "metric": "absolute-error",
        "upper_bound": 0.8,
        "unit": "K",
        "composition": "ADDITIVE_ABSOLUTE"
      },
      "notes": ""
    },
    {
      "property_id": "mesh.cell-identity.v1",
      "effect": "FORGET",
      "output_property_id": null,
      "transform_id": null,
      "bound": null,
      "notes": ""
    }
  ],
  "notes": ""
}
~~~

This is a declaration. It is not yet a verification occurrence.

## 3. Information loss is explicit

FORGET is deliberately one-way in V1.

If a tracked source property is forgotten by the first contract in a chain, the
composition retains FORGET for that source property. A later transformation
cannot manufacture the missing distinction merely by naming it.

New evidence can support a new state or representation through a separate
operation, but V1 contract composition does not treat downstream generation as
recovery.

This implements a narrow architectural rule:

> Pure downstream transformation cannot silently recreate a distinction that an
> earlier declared transformation explicitly forgot.

This is an architectural rule for the retained contracts, not a universal
information-theory theorem.

## 4. Partial composition algebra

For

~~~text
X --T1--> Y --T2--> Z
~~~

ciw.preservation-composition.v1 first requires the exact codomain
representation of T1 to equal the exact domain representation of T2.

It then checks every requirement declared by T2.

A requirement is:

- PASS if T1 explicitly makes that property available;
- FAIL if T1 explicitly forgets or transforms away that exact property;
- UNRESOLVED if T1 says nothing sufficient about it.

The complete composition is:

- REFUSED if any requirement fails;
- UNRESOLVED if any requirement or tracked property lineage lacks a V1 rule;
- COMPOSABLE only when all downstream requirements are established and every
  tracked left-hand lineage has a supported composition rule.

### Implemented lineage rules

V1 supports the following narrow cases.

~~~text
PRESERVE ; PRESERVE  -> PRESERVE

PRESERVE ; TRANSFORM -> TRANSFORM
TRANSFORM ; PRESERVE -> TRANSFORM
TRANSFORM ; TRANSFORM -> TRANSFORM
    with the named transform chain retained

BOUND ; PRESERVE     -> BOUND
PRESERVE ; BOUND     -> BOUND

BOUND ; BOUND
    -> BOUND only when:
       metric is identical
       unit is identical
       both rules are ADDITIVE_ABSOLUTE

FORGET ; anything
    -> FORGET
~~~

Compatible additive absolute bounds compose as:

~~~text
epsilon_total = epsilon_1 + epsilon_2
~~~

V1 intentionally does **not** invent a rule for cases such as:

~~~text
BOUND ; TRANSFORM
TRANSFORM ; BOUND
incompatible metrics
incompatible units
unknown downstream property treatment
~~~

Those remain UNKNOWN, which makes the composition UNRESOLVED.

There is no claim that these V1 rules form a universal preservation algebra.

## 5. Typed verification receipts

ciw.preservation-verification.v1 binds:

- the exact preservation contract;
- a source-state content identity;
- a candidate-state content identity;
- one check for every contract requirement and effect.

Every check records:

- obligation kind;
- property identity;
- VERIFIED, REFUTED or UNRESOLVED;
- verification method class;
- evidence reference when resolved;
- notes.

V1 method classes are:

- FORMAL_PROOF;
- EXACT_ALGEBRA;
- NUMERICAL_BOUND;
- STATISTICAL_BOUND;
- EMPIRICAL_TEST;
- CALIBRATION_EVIDENCE;
- HUMAN_ATTESTATION;
- NOT_PERFORMED.

A resolved check cannot use NOT_PERFORMED.

The method classes are deliberately retained rather than flattened into one
generic PASS. A finite numerical test is not silently promoted to formal proof,
and a formal proof does not establish physical calibration.

The aggregate status is:

~~~text
any REFUTED     -> REFUTED
all VERIFIED    -> VERIFIED
otherwise       -> UNRESOLVED
~~~

Verification remains distinct from admission.

## 6. Admission eligibility, not admission

ciw.preservation-admission-gate.v1 consumes one contract, its exact
verification receipt and a small loss policy.

The V1 policy names properties whose loss is forbidden.

The decision is:

~~~text
forbidden declared loss     -> REFUSED
refuted verification        -> REFUSED
protected effect undeclared -> UNRESOLVED
unresolved verification     -> UNRESOLVED
otherwise                   -> ELIGIBLE
~~~

Every property named in `forbidden_forgets` must have an explicit effect in
the contract. An unmentioned property is unknown even when every declared
obligation is VERIFIED. Listing it only in `requires` establishes an input
obligation, not its output preservation. The unknown case retains no fabricated
loss violation and cannot produce eligibility. A disclosed TRANSFORM or BOUND
still follows its declared effect and verification; this policy does not
upgrade either to exact PRESERVE. Gate validation recomputes the same policy,
so resealing an edited ELIGIBLE decision cannot bypass unresolved coverage.

Even ELIGIBLE retains:

~~~text
state_admission_performed = false
canonical_state_mutated = false
execution_authority = false
~~~

A later state-admission authority may consume such a receipt. This increment
does not implement that authority.

## 7. CLI

Create a contract:

~~~sh
net preservation create \
  registry.json \
  preservation-spec.json \
  --output preservation-contract.json
~~~

Compose two contracts:

~~~sh
net preservation compose \
  registry.json \
  left-contract.json \
  right-contract.json \
  --output composition.json
~~~

Create a verification receipt:

~~~sh
net preservation verify \
  registry.json \
  preservation-contract.json \
  verification-spec.json \
  --output verification.json
~~~

Evaluate admission eligibility:

~~~sh
net preservation gate \
  registry.json \
  preservation-contract.json \
  verification.json \
  loss-policy.json \
  --output admission-gate.json
~~~

Negative or unresolved composition, verification and gate decisions are retained
rather than erased. Their CLI commands return a nonzero decision code without
turning the valid negative record into malformed input.

## 8. Relationship to the existing finite preservation checker

The existing finite checker performs actual exact rational calculations for a
specific declared finite model, including statewise intervention commutativity,
pushforwards, conditional expectations and retained counterexamples.

This preservation-contract increment sits one level above that kind of
domain-specific mathematics.

A future adapter can use a qualifying finite-preservation witness as evidence
for one or more typed verification obligations. V1 does **not** automatically
reinterpret an arbitrary finite witness as proof of an arbitrary property.

That separation is intentional:

~~~text
preservation contract
      ↓ says what must be established
domain-specific verifier
      ↓ establishes or refutes a scoped claim
verification receipt
      ↓ retains method + evidence
eligibility gate
~~~

## 9. Machine intelligence position

This layer is designed so that an LLM, optimizer, Julia program, simulator or
human can propose a candidate without acquiring the authority to redefine its
acceptance conditions.

Conceptually:

~~~text
S_t --proposal/search--> S_hat_(t+1)

preservation contract + evidence
                ↓
           verification
                ↓
      VERIFIED / REFUTED / UNRESOLVED
                ↓
        admission eligibility
~~~

Rejected and unresolved candidates can remain retained evidence while the
canonical state remains unchanged.

## Scope and non-claims

V1 does not:

- mutate or admit canonical state;
- execute providers;
- prove the existing scientific morphism in general;
- infer physical validity from a typed contract;
- infer causality;
- restore forgotten information;
- compose arbitrary error metrics;
- compose arbitrary probabilistic guarantees;
- implement theorem proving;
- implement a universal category of scientific transformations;
- give an LLM or optimizer acceptance authority.

The next useful increments are domain-specific verification adapters and Board
projection: bind a Board node to its preservation contract, expose effects in
the visual Board, and let candidate transitions carry preservation receipts
through the existing Needle / verification / admission boundaries.
