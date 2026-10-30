---------------------------- MODULE FlowGCTransfer ----------------------------
EXTENDS Naturals, FiniteSets

\* A bounded, sequentially consistent DESIGN model, not a PCC implementation.
\* Credits identify owning slots; identity may map distinct credits to one object.
\* Each Transfer is atomic with respect to scans. This models the intended graph
\* lock boundary; it does not verify the native lock or its memory ordering.
\* Referents are leaves, the output stays rooted, and there is one marking cycle.

CONSTANTS MaxSources, ObjectIds, UseBarrier

ASSUME /\ MaxSources \in Nat \ {0}
       /\ IsFiniteSet(ObjectIds)
       /\ ObjectIds # {}
       /\ UseBarrier \in BOOLEAN

VARIABLES sourceCount, identity, roots, fields, length,
          outputScanned, fieldsScanned, scannedRoots,
          marked, marking, missed, published

vars == <<sourceCount, identity, roots, fields, length,
          outputScanned, fieldsScanned, scannedRoots,
          marked, marking, missed, published>>

Slots == 1..sourceCount
VisibleTokens == ({roots[i] : i \in Slots} \ {0})
                 \cup ({fields[i] : i \in 1..length} \ {0})
VisibleIdentities == {identity[t] : t \in VisibleTokens}

Init ==
    /\ sourceCount \in 1..MaxSources
    /\ identity \in [Slots -> ObjectIds]
    /\ roots = [i \in Slots |-> i]
    /\ fields = [i \in Slots |-> 0]
    /\ length = 0
    /\ outputScanned = FALSE
    /\ fieldsScanned = FALSE
    /\ scannedRoots = {}
    /\ marked = {}
    /\ marking = TRUE
    /\ missed = {}
    /\ published = FALSE

ScanOutput ==
    /\ marking
    /\ ~outputScanned
    /\ outputScanned' = TRUE
    /\ UNCHANGED <<sourceCount, identity, roots, fields, length,
                   fieldsScanned, scannedRoots, marked, marking, missed,
                   published>>

ScanFields ==
    /\ marking
    /\ outputScanned
    /\ ~fieldsScanned
    /\ marked' = marked \cup {identity[fields[i]] : i \in 1..length}
    /\ fieldsScanned' = TRUE
    /\ UNCHANGED <<sourceCount, identity, roots, fields, length,
                   outputScanned, scannedRoots, marking, missed, published>>

ScanRoot(i) ==
    /\ marking
    /\ i \in Slots \ scannedRoots
    /\ marked' = IF roots[i] = 0
                  THEN marked
                  ELSE marked \cup {identity[roots[i]]}
    /\ scannedRoots' = scannedRoots \cup {i}
    /\ UNCHANGED <<sourceCount, identity, roots, fields, length,
                   outputScanned, fieldsScanned, marking, missed, published>>

Transfer ==
    /\ length < sourceCount
    /\ ~published
    /\ LET i == length + 1
       IN /\ roots[i] = i
          /\ fields' = [fields EXCEPT ![i] = roots[i]]
          /\ roots' = [roots EXCEPT ![i] = 0]
          /\ marked' = IF UseBarrier /\ marking /\ fieldsScanned
                        THEN marked \cup {identity[i]}
                        ELSE marked
    /\ length' = length + 1
    /\ UNCHANGED <<sourceCount, identity, outputScanned, fieldsScanned,
                   scannedRoots, marking, missed, published>>

Sweep ==
    /\ marking
    /\ outputScanned
    /\ fieldsScanned
    /\ scannedRoots = Slots
    /\ missed' = VisibleIdentities \ marked
    /\ marking' = FALSE
    /\ UNCHANGED <<sourceCount, identity, roots, fields, length,
                   outputScanned, fieldsScanned, scannedRoots, marked,
                   published>>

\* Transfers after Sweep are allowed, as in mark_schedules(). Publish is an
\* explicit terminal bookkeeping step after all transfers and scans, matching
\* mark_schedule()'s final construction.publish(). No liveness claim is made.
Publish ==
    /\ ~marking
    /\ length = sourceCount
    /\ ~published
    /\ published' = TRUE
    /\ UNCHANGED <<sourceCount, identity, roots, fields, length,
                   outputScanned, fieldsScanned, scannedRoots, marked,
                   marking, missed>>

Done == published /\ UNCHANGED vars

Next == ScanOutput \/ ScanFields \/ (\E i \in Slots : ScanRoot(i))
        \/ Transfer \/ Sweep \/ Publish \/ Done

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ sourceCount \in 1..MaxSources
    /\ identity \in [Slots -> ObjectIds]
    /\ roots \in [Slots -> 0..sourceCount]
    /\ fields \in [Slots -> 0..sourceCount]
    /\ length \in 0..sourceCount
    /\ outputScanned \in BOOLEAN
    /\ fieldsScanned \in BOOLEAN
    /\ scannedRoots \subseteq Slots
    /\ marked \subseteq ObjectIds
    /\ marking \in BOOLEAN
    /\ missed \subseteq ObjectIds
    /\ published \in BOOLEAN

OwnershipConserved ==
    \A token \in Slots :
        Cardinality({i \in Slots : roots[i] = token})
        + Cardinality({i \in Slots : fields[i] = token}) = 1

PrefixVisible ==
    \A i \in Slots :
        /\ (i <= length => fields[i] = i /\ roots[i] = 0)
        /\ (i > length => fields[i] = 0 /\ roots[i] = i)

AllOwnedVisible == VisibleTokens = Slots
ScanOrder == /\ (fieldsScanned => outputScanned)
             /\ (~marking => fieldsScanned /\ scannedRoots = Slots)
PublishedComplete == published => length = sourceCount /\ ~marking
NoLostLiveObjects == missed = {}

=============================================================================
