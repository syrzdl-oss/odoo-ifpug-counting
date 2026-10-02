# Transaction functions (written to <urn:functions> with their declaring modules).
#   3_CRUD_c / 3_CRUD_w / 3_CRUD_u : maintenance EIs (create, write, delete) per table and form view
#   7_Wizard_EI                    : wizard EI per transient model with a reachable form
#   4_EQ                           : EQ per table and list field set
#   5_EO_report                    : report EO (DET = deduplicated template outputs + 2)
#   6_EO_analysis                  : analysis EO per pivot / graph view
# Report EO and wizard EI FTRs come from the measured means (:meanFtr), rounded.
PREFIX : <http://example.org/odoo-ifpug#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

INSERT {
  GRAPH <urn:functions> {
    ?fn a ?cls ;
        :onFile ?srcNode ;
        :inModule ?targetModule ;
        :byRule ?branch ;
        :eiOp ?byRule ;
        :det ?detv ;
        :retCount ?retv ;
        :ftrCount ?ftrv ;
        :complexity ?cx ;
        :ufp ?ufp ;
        :weight ?ufp ;
        :declaredBy ?declarer ;
        :countingSide "declarative" .
  }
}
WHERE {
  { SELECT ?targetModule ?branch ?item ?ufp ?cls ?srcNode ?detv ?retv ?ftrv ?byRule ?cx ?declarer WHERE {

    # ---------- (3c) maintenance EI: create ----------
    { { SELECT ?m4c ?v4C (?w AS ?ufp) (?det4 AS ?detv) (?ftr4 AS ?ftrv) WHERE {
        { SELECT ?m4c ?v4C (MAX(?dv) AS ?det4) (MAX(IF(?g=true,1,0)) AS ?anyg) WHERE {
            ?a4C rdf:type :WindowAction ; :actsOn ?m4c ; :reachKind ?rkC ; :usesView ?v4C .
                FILTER(?rkC != "none")
                ?v4C :viewType "form" ; :gateCreate ?gC .
                { SELECT ?v4C (COUNT(DISTINCT ?fdC) AS ?nfC) WHERE {
                    ?v4C :ofModel ?vmC ; :viewDetField ?fdC .
                    FILTER NOT EXISTS { ?v4C :viewDetFieldNotOnCreate ?fdC }
                    FILTER NOT EXISTS { ?fdC :ttype ?ctC ; :relatesTo ?chC . ?chC :foldedInto ?vmC .
                                        FILTER(?ctC IN ("one2many","many2many")) }
                  } GROUP BY ?v4C }
                OPTIONAL { SELECT ?v4C (COUNT(DISTINCT ?btC) AS ?nbC) WHERE {
                    ?v4C :hasButton ?btC } GROUP BY ?v4C }
                BIND(?nfC + 2 AS ?dv) BIND(?gC AS ?g)
            ?m4c rdf:type :PersistentModel .
          } GROUP BY ?m4c ?v4C }
        OPTIONAL { SELECT ?v4C (COUNT(DISTINCT ?ft) AS ?nftrC) WHERE {
            ?v4C :viewFtrRef ?ft0 . FILTER NOT EXISTS { ?v4C :viewFtrRefNotOnCreate ?ft0 }
            OPTIONAL { ?ft0 :foldedInto ?ftpC } BIND(COALESCE(?ftpC, ?ft0) AS ?ft)
            ?ft rdf:type :PersistentModel .
            FILTER(EXISTS { ?fv :belongsTo ?ft ; :isUserVisible true } ||
                   EXISTS { ?dx rdf:type :WindowAction ; :actsOn ?ft ; :reachKind ?rkx . FILTER(?rkx != "none") })
          } GROUP BY ?v4C }
        BIND(IF(COALESCE(?nftrC, 0) < 1, 1, ?nftrC) AS ?basecount)
        FILTER(?anyg = 1)
        BIND(?basecount AS ?ftr4)
        BIND(IF(?det4<=4,0,IF(?det4<=15,1,2)) AS ?d4)
        BIND(IF(?ftr4<=1,0,IF(?ftr4=2,1,2)) AS ?f4)
        BIND(IF(?d4=0&&?f4=0,3,IF(?d4=1&&?f4=0,3,IF(?d4=2&&?f4=0,4,
             IF(?d4=0&&?f4=1,3,IF(?d4=1&&?f4=1,4,IF(?d4=2&&?f4=1,6,
             IF(?d4=0&&?f4=2,4,IF(?d4=1&&?f4=2,6,6)))))))) AS ?w)
      } }
      ?m4c :hostModule ?targetModule .
      OPTIONAL { ?axC rdf:type :WindowAction ; :actsOn ?m4c ; :reachKind ?rkxC ; :usesView ?vxC ;
                 :declaredIn ?declarer . FILTER(?rkxC != "none") ?vxC :viewType "form" . }
      BIND("3_CRUD_c" AS ?branch) BIND(CONCAT(STR(?m4c),"#c#",STR(?v4C)) AS ?item)
      BIND(:EI AS ?cls) BIND(?m4c AS ?srcNode) BIND("create" AS ?byRule)
      BIND(IF(?ufp=3,"low",IF(?ufp=4,"average","high")) AS ?cx) }

    # ---------- (3w) maintenance EI: write ----------
    UNION
    { { SELECT ?m4w ?v4W (?w AS ?ufp) (?det4 AS ?detv) (?ftr4 AS ?ftrv) WHERE {
        { SELECT ?m4w ?v4W (MAX(?dv) AS ?det4) (MAX(IF(?g=true,1,0)) AS ?anyg) WHERE {
            ?a4W rdf:type :WindowAction ; :actsOn ?m4w ; :reachKind ?rkW ; :usesView ?v4W .
                FILTER(?rkW != "none")
                ?v4W :viewType "form" ; :gateWrite ?gW .
                { SELECT ?v4W (COUNT(DISTINCT ?fdW) AS ?nfW) WHERE {
                    ?v4W :ofModel ?vmW ; :viewDetField ?fdW .
                    FILTER NOT EXISTS { ?v4W :viewDetFieldNotOnEdit ?fdW }
                    FILTER NOT EXISTS { ?fdW :ttype ?ctW ; :relatesTo ?chW . ?chW :foldedInto ?vmW .
                                        FILTER(?ctW IN ("one2many","many2many")) }
                  } GROUP BY ?v4W }
                OPTIONAL { SELECT ?v4W (COUNT(DISTINCT ?btW) AS ?nbW) WHERE {
                    ?v4W :hasButton ?btW } GROUP BY ?v4W }
                BIND(?nfW + 2 AS ?dv) BIND(?gW AS ?g)
            ?m4w rdf:type :PersistentModel .
          } GROUP BY ?m4w ?v4W }
        OPTIONAL { SELECT ?v4W (COUNT(DISTINCT ?ft) AS ?nftrW) WHERE {
            ?v4W :viewFtrRef ?ft0 . FILTER NOT EXISTS { ?v4W :viewFtrRefNotOnEdit ?ft0 }
            OPTIONAL { ?ft0 :foldedInto ?ftpW } BIND(COALESCE(?ftpW, ?ft0) AS ?ft)
            ?ft rdf:type :PersistentModel .
            FILTER(EXISTS { ?fv :belongsTo ?ft ; :isUserVisible true } ||
                   EXISTS { ?dx rdf:type :WindowAction ; :actsOn ?ft ; :reachKind ?rkx . FILTER(?rkx != "none") })
          } GROUP BY ?v4W }
        BIND(IF(COALESCE(?nftrW, 0) < 1, 1, ?nftrW) AS ?basecount)
        FILTER(?anyg = 1)
        BIND(?basecount AS ?ftr4)
        BIND(IF(?det4<=4,0,IF(?det4<=15,1,2)) AS ?d4)
        BIND(IF(?ftr4<=1,0,IF(?ftr4=2,1,2)) AS ?f4)
        BIND(IF(?d4=0&&?f4=0,3,IF(?d4=1&&?f4=0,3,IF(?d4=2&&?f4=0,4,
             IF(?d4=0&&?f4=1,3,IF(?d4=1&&?f4=1,4,IF(?d4=2&&?f4=1,6,
             IF(?d4=0&&?f4=2,4,IF(?d4=1&&?f4=2,6,6)))))))) AS ?w)
      } }
      ?m4w :hostModule ?targetModule .
      OPTIONAL { ?axW rdf:type :WindowAction ; :actsOn ?m4w ; :reachKind ?rkxW ; :usesView ?vxW ;
                 :declaredIn ?declarer . FILTER(?rkxW != "none") ?vxW :viewType "form" . }
      BIND("3_CRUD_w" AS ?branch) BIND(CONCAT(STR(?m4w),"#w#",STR(?v4W)) AS ?item)
      BIND(:EI AS ?cls) BIND(?m4w AS ?srcNode) BIND("write" AS ?byRule)
      BIND(IF(?ufp=3,"low",IF(?ufp=4,"average","high")) AS ?cx) }

    # ---------- (3u) maintenance EI: delete ----------
    UNION
    { { SELECT ?m4u ?v4U (?w AS ?ufp) (?det4 AS ?detv) (?ftr4 AS ?ftrv) WHERE {
        { SELECT ?m4u ?v4U (MAX(?dv) AS ?det4) (MAX(IF(?g=true,1,0)) AS ?anyg) WHERE {
            ?a4U rdf:type :WindowAction ; :actsOn ?m4u ; :reachKind ?rkU ; :usesView ?v4U .
                FILTER(?rkU != "none")
                ?v4U :viewType "form" ; :gateUnlink ?gU .
                { SELECT ?v4U (COUNT(DISTINCT ?fdU) AS ?nfU) WHERE {
                    ?v4U :ofModel ?vmU ; :viewDetField ?fdU .
                    FILTER NOT EXISTS { ?v4U :viewDetFieldNotOnEdit ?fdU }
                    FILTER NOT EXISTS { ?fdU :ttype ?ctU ; :relatesTo ?chU . ?chU :foldedInto ?vmU .
                                        FILTER(?ctU IN ("one2many","many2many")) }
                  } GROUP BY ?v4U }
                OPTIONAL { SELECT ?v4U (COUNT(DISTINCT ?btU) AS ?nbU) WHERE {
                    ?v4U :hasButton ?btU } GROUP BY ?v4U }
                BIND(?nfU + 2 AS ?dv) BIND(?gU AS ?g)
            ?m4u rdf:type :PersistentModel .
          } GROUP BY ?m4u ?v4U }
        OPTIONAL { SELECT ?v4U (COUNT(DISTINCT ?ft) AS ?nftrU) WHERE {
            ?v4U :viewFtrRef ?ft0 .
            OPTIONAL { ?ft0 :foldedInto ?ftpU } BIND(COALESCE(?ftpU, ?ft0) AS ?ft)
            ?ft rdf:type :PersistentModel .
            FILTER(EXISTS { ?fv :belongsTo ?ft ; :isUserVisible true } ||
                   EXISTS { ?dx rdf:type :WindowAction ; :actsOn ?ft ; :reachKind ?rkx . FILTER(?rkx != "none") })
          } GROUP BY ?v4U }
        BIND(1 AS ?basecount)
        FILTER(?anyg = 1)
        BIND(?basecount AS ?ftr4)
        BIND(IF(?det4<=4,0,IF(?det4<=15,1,2)) AS ?d4)
        BIND(IF(?ftr4<=1,0,IF(?ftr4=2,1,2)) AS ?f4)
        BIND(IF(?d4=0&&?f4=0,3,IF(?d4=1&&?f4=0,3,IF(?d4=2&&?f4=0,4,
             IF(?d4=0&&?f4=1,3,IF(?d4=1&&?f4=1,4,IF(?d4=2&&?f4=1,6,
             IF(?d4=0&&?f4=2,4,IF(?d4=1&&?f4=2,6,6)))))))) AS ?w)
      } }
      ?m4u :hostModule ?targetModule .
      OPTIONAL { ?axU rdf:type :WindowAction ; :actsOn ?m4u ; :reachKind ?rkxU ; :usesView ?vxU ;
                 :declaredIn ?declarer . FILTER(?rkxU != "none") ?vxU :viewType "form" . }
      BIND("3_CRUD_u" AS ?branch) BIND(CONCAT(STR(?m4u),"#u#",STR(?v4U)) AS ?item)
      BIND(:EI AS ?cls) BIND(?m4u AS ?srcNode) BIND("unlink" AS ?byRule)
      BIND(IF(?ufp=3,"low",IF(?ufp=4,"average","high")) AS ?cx) }

    # ---------- (4) EQ: list views ----------
    UNION
    { { SELECT ?m5 ?eqv (?w AS ?ufp) (?det5f AS ?detv) (?ftr5 AS ?ftrv) WHERE {
        { SELECT ?m5 ?eqv (MAX(?nl) AS ?nList) WHERE {
            { SELECT ?m5 ?a6 (GROUP_CONCAT(?ldn; separator=",") AS ?eqv)
                     (COUNT(DISTINCT ?ld) AS ?nl) WHERE {
                ?a6 rdf:type :WindowAction ; :actsOn ?m5 ; :reachKind ?rk .
                FILTER(?rk != "none")
                ?m5 rdf:type :PersistentModel .
                ?a6 :usesView ?v6 . ?v6 :viewType ?vt6 . FILTER(?vt6 IN ("list","kanban"))
                ?v6 :viewDetField ?ld . ?ld :fieldName ?ldn
              } GROUP BY ?m5 ?a6 }
          } GROUP BY ?m5 ?eqv }
        BIND(?nList + 2 AS ?det5)
        OPTIONAL { SELECT ?m5 ?eqv (MAX(?fc) AS ?maxftr) WHERE {
            SELECT ?m5 ?eqv ?a7 (COUNT(DISTINCT ?ft) AS ?fc) WHERE {
              ?a7 rdf:type :WindowAction ; :actsOn ?m5 ; :reachKind ?rk2 ; :usesView ?v7 .
              ?v7 :viewType ?vt7 . FILTER(?vt7 IN ("list","kanban")) ?v7 :viewFtrRef ?ft0 .
              OPTIONAL { ?ft0 :foldedInto ?ftpQ } BIND(COALESCE(?ftpQ, ?ft0) AS ?ft)
              FILTER(?rk2 != "none")
              { SELECT ?a7 (GROUP_CONCAT(?ldn2; separator=",") AS ?eqv) WHERE {
                  ?a7 :usesView ?v7b . ?v7b :viewType ?vt7b . FILTER(?vt7b IN ("list","kanban"))
                  ?v7b :viewDetField ?ld2 . ?ld2 :fieldName ?ldn2 } GROUP BY ?a7 }
            } GROUP BY ?m5 ?eqv ?a7 } GROUP BY ?m5 ?eqv }
        BIND(IF(?det5=0,6,?det5) AS ?det5f)
        BIND(IF(COALESCE(?maxftr,0) < 1, 1, COALESCE(?maxftr,0)) AS ?ftr5)
        BIND(IF(?det5f<=5,0,IF(?det5f<=19,1,2)) AS ?d5)
        BIND(IF(?ftr5<=1,0,IF(?ftr5<=3,1,2)) AS ?f5)
        BIND(IF(?d5=0&&?f5=0,3,IF(?d5=1&&?f5=0,3,IF(?d5=2&&?f5=0,4,
             IF(?d5=0&&?f5=1,3,IF(?d5=1&&?f5=1,4,IF(?d5=2&&?f5=1,6,
             IF(?d5=0&&?f5=2,4,IF(?d5=1&&?f5=2,6,6)))))))) AS ?w)
      } }
      ?m5 :hostModule ?targetModule .
      OPTIONAL { ?axQ rdf:type :WindowAction ; :actsOn ?m5 ; :reachKind ?rkxQ ;
                 :declaredIn ?declarer . FILTER(?rkxQ != "none") }
      BIND("4_EQ" AS ?branch) BIND(CONCAT(STR(?m5),"#EQ#",?eqv) AS ?item)
      BIND(:EQ AS ?cls) BIND(?m5 AS ?srcNode)
      BIND(IF(?ufp=3,"low",IF(?ufp=4,"average","high")) AS ?cx) }

    # ---------- (5) EO: printable reports ----------
    UNION
    { { SELECT ?it6 ?m6 (?w AS ?ufp) (?det6 AS ?detv) WHERE {
        ?it6 rdf:type :ReportAction ; :actsOn ?m6 .
        ?m6 rdf:type :PersistentModel .
        OPTIONAL { ?it6 :usesTemplate ?tpl . ?tpl :nOutputDistinct ?nall . }
        BIND(IF(BOUND(?nall), ?nall, 10) + 2 AS ?det6)
        BIND(IF(?det6<=5,0,IF(?det6<=19,1,2)) AS ?d6)
        OPTIONAL { SELECT (MAX(?mf6_x) AS ?mf6) WHERE { ?ce6 rdf:type :CalibrationStat ; :entryKind "eo_report" ; :meanFtr ?mf6_x } }
        BIND(xsd:integer(ROUND(COALESCE(?mf6, 2))) AS ?mf6_r)
                BIND(IF(?mf6_r <= 1, 0, IF(?mf6_r <= 3, 1, 2)) AS ?fq6)
        BIND(IF(?d6 + ?fq6 <= 1, 4, IF(?d6 + ?fq6 <= 2, 5, 7)) AS ?w)
      } }
      ?it6 :actsOn ?m6x . ?m6x :hostModule ?targetModule .
      OPTIONAL { ?it6 :declaredIn ?declarer }
      BIND("5_EO_report" AS ?branch) BIND(STR(?it6) AS ?item)
      BIND(:EO AS ?cls) BIND(?m6 AS ?srcNode)
      OPTIONAL { SELECT (MAX(?mfe_x) AS ?mfe) WHERE { ?ce rdf:type :CalibrationStat ; :entryKind "eo_report" ; :meanFtr ?mfe_x } }
      BIND(xsd:integer(ROUND(COALESCE(?mfe, 2))) AS ?ftrv)
      BIND(IF(?ufp=4,"low",IF(?ufp=5,"average","high")) AS ?cx) }

    # ---------- (6) EO: pivot / graph analysis screens ----------
    UNION
    { { SELECT ?v8 ?m7 (?w AS ?ufp) (?det7 AS ?detv) (?ftr7 AS ?ftrv) WHERE {
        ?a8 rdf:type :WindowAction ; :actsOn ?m7 ; :reachKind ?rk ; :usesView ?v8 .
        FILTER(?rk != "none")
        ?m7 rdf:type :PersistentModel .
        ?v8 :viewType ?vt8 . FILTER(?vt8 IN ("pivot", "graph", "cohort"))
        { SELECT ?v8 (COUNT(DISTINCT ?ms) AS ?nMs) WHERE {
            ?v8 :viewDetField ?ms } GROUP BY ?v8 }
        FILTER(?nMs > 0)
        BIND(?nMs + 2 AS ?det7)
        BIND(IF(?det7 <= 5, 0, IF(?det7 <= 19, 1, 2)) AS ?d7)
        OPTIONAL { SELECT ?v8 (COUNT(DISTINCT ?aft) AS ?nAft) WHERE {
            ?v8 :viewFtrRef ?aft0 .
            OPTIONAL { ?aft0 :foldedInto ?aftp } BIND(COALESCE(?aftp, ?aft0) AS ?aft)
          } GROUP BY ?v8 }
        BIND(IF(COALESCE(?nAft, 0) < 1, 1, ?nAft) AS ?ftr7)
        BIND(IF(?ftr7 <= 1, 0, IF(?ftr7 <= 3, 1, 2)) AS ?fq7)
        BIND(IF(?d7 + ?fq7 <= 1, 4, IF(?d7 + ?fq7 <= 2, 5, 7)) AS ?w)
      } }
      ?m7 :hostModule ?targetModule .
      OPTIONAL { ?axA rdf:type :WindowAction ; :actsOn ?m7 ; :usesView ?v8 ;
                 :reachKind ?rkxA ; :declaredIn ?declarer . FILTER(?rkxA != "none") }
      BIND("6_EO_analysis" AS ?branch) BIND(CONCAT(STR(?v8),"#EOa") AS ?item)
      BIND(:EO AS ?cls) BIND(?m7 AS ?srcNode)
      BIND(IF(?ufp=4,"low",IF(?ufp=5,"average","high")) AS ?cx) }

    # ---------- (7) wizard EI: transient forms (DET declared, FTR measured) ----------
    UNION
    { { SELECT ?m9 (?w AS ?ufp) (?det9 AS ?detv) (?ftr9 AS ?ftrv) WHERE {
        { SELECT ?m9 (MAX(?dv) AS ?det9) WHERE {
            ?a9 rdf:type :WindowAction ; :actsOn ?m9 ; :reachKind ?rk ; :usesView ?v9 .
              ?v9 :viewType "form" .
              { SELECT ?v9 (COUNT(DISTINCT ?fd9) + 2 AS ?dv) WHERE {
                  ?v9 :ofModel ?vm9 ; :viewDetField ?fd9 .
                  FILTER NOT EXISTS { ?fd9 :ttype ?ct9 ; :relatesTo ?ch9 . ?ch9 :foldedInto ?vm9 .
                                      FILTER(?ct9 IN ("one2many","many2many")) }
                } GROUP BY ?v9 }
            FILTER(?rk != "none")
            ?m9 rdf:type :TransientModel . } GROUP BY ?m9 }
        OPTIONAL { SELECT (MAX(?mfw_x) AS ?mfw) WHERE { ?cw rdf:type :CalibrationStat ; :entryKind "wizard" ; :txCategory "EI" ; :meanFtr ?mfw_x } }
        BIND(xsd:integer(ROUND(COALESCE(?mfw, 2))) AS ?ftr9)
        BIND(IF(?det9<=4,0,IF(?det9<=15,1,2)) AS ?d9)
        BIND(IF(?ftr9<=1,0,IF(?ftr9=2,1,2)) AS ?f9)
        BIND(IF(?d9=0&&?f9=0,3,IF(?d9=1&&?f9=0,3,IF(?d9=2&&?f9=0,4,
             IF(?d9=0&&?f9=1,3,IF(?d9=1&&?f9=1,4,IF(?d9=2&&?f9=1,6,
             IF(?d9=0&&?f9=2,4,IF(?d9=1&&?f9=2,6,6)))))))) AS ?w)
      } }
      ?m9 :hostModule ?targetModule .
      OPTIONAL { ?axZ rdf:type :WindowAction ; :actsOn ?m9 ; :reachKind ?rkxZ ; :usesView ?vxZ ;
                 :declaredIn ?declarer . FILTER(?rkxZ != "none") ?vxZ :viewType "form" . }
      BIND("7_Wizard_EI" AS ?branch) BIND(CONCAT(STR(?m9),"#WIZ") AS ?item)
      BIND(:EI AS ?cls) BIND(?m9 AS ?srcNode) BIND("wizard" AS ?byRule)
      BIND(IF(?ufp=3,"low",IF(?ufp=4,"average","high")) AS ?cx) }
  } }
  BIND(IRI(CONCAT("urn:fn:", ENCODE_FOR_URI(?item))) AS ?fn)
}
