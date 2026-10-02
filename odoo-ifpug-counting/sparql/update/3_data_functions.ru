# Data functions per module.
#   ILF: a persistent, user-recognizable table that is not a subgroup, counted for its host
#        module and for every application that maintains it. DET = visible stored fields
#        (relational fields counted once per target), RET = 1 + subgroups.
#   EIF: a table referenced by the module (relational field or view FTR) that the module
#        neither hosts nor maintains. DET = 1 + exposed fields, RET = record types.
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
    # ---------- (1) ILF: tables hosted or maintained by the module ----------
    { { SELECT ?m1 (?w AS ?ufp) (?det AS ?detv) (?ret AS ?retv) WHERE {
        ?m1 rdf:type :PersistentModel .
        FILTER NOT EXISTS { ?m1 :foldedInto ?p1 }
        FILTER(EXISTS { ?fv1 :belongsTo ?m1 ; :isUserVisible true } ||
               EXISTS { ?d1 rdf:type :WindowAction ; :actsOn ?m1 ; :reachKind ?rk1 . FILTER(?rk1 != "none") })
        OPTIONAL { SELECT ?m1 (COUNT(DISTINCT ?detKey) AS ?det0) WHERE {
            ?f1 :belongsTo ?owner ; :fieldName ?fname ; :isUserVisible true .
            ?owner :foldedInto? ?m1 .
            FILTER NOT EXISTS { ?f1 :isComputed true . FILTER NOT EXISTS { ?f1 :store true } }
            FILTER NOT EXISTS { ?f1 :isRelated true }
            OPTIONAL { ?f1 :ttype ?tt1 }
            FILTER(!BOUND(?tt1) || (?tt1 != "one2many" && ?tt1 != "many2many"))
            FILTER NOT EXISTS { ?f1 :relatesTo ?m1 . ?owner :foldedInto ?m1 }
            OPTIONAL { ?f1 :relatesTo ?rel1 }
            BIND(IF(BOUND(?rel1), STR(?rel1), CONCAT(STR(?owner), "#", ?fname)) AS ?detKey)
          } GROUP BY ?m1 }
        OPTIONAL { SELECT ?m1 (COUNT(?c1) AS ?childcount) WHERE { ?c1 :foldedInto ?m1 } GROUP BY ?m1 }
        BIND(COALESCE(?det0,0) AS ?det)
        BIND(1 + COALESCE(?childcount,0) AS ?ret)
        BIND(IF(?det<=19,0,IF(?det<=50,1,2)) AS ?d)
        BIND(IF(?ret<=1,0,IF(?ret<=5,1,2)) AS ?r)
        BIND(IF(?r=0&&?d=0,7,IF(?r=1&&?d=0,7,IF(?r=2&&?d=0,10,
             IF(?r=0&&?d=1,7,IF(?r=1&&?d=1,10,IF(?r=2&&?d=1,15,
             IF(?r=0&&?d=2,10,IF(?r=1&&?d=2,15,15)))))))) AS ?w)
      } }
      { ?m1 :hostModule ?targetModule } UNION { ?m1 :maintainedBy ?targetModule }
      BIND("1_ILF" AS ?branch) BIND(CONCAT(STR(?m1),"#ILF#",STR(?targetModule)) AS ?item)
      BIND(:ILF AS ?cls) BIND(?m1 AS ?srcNode)
      BIND(IF(?ufp=7,"low",IF(?ufp=10,"average","high")) AS ?cx) }

    # ---------- (2) EIF: tables referenced but neither hosted nor maintained ----------
    UNION
    { { SELECT ?targetModule ?m2 (?w AS ?ufp) (?det2 AS ?detv) (?ret2 AS ?ret2out) WHERE {
        { SELECT DISTINCT ?targetModule ?m2 WHERE {
            ?ilf2 rdf:type :PersistentModel ; :hostModule ?targetModule .
            FILTER NOT EXISTS { ?ilf2 :foldedInto ?p2 }
            FILTER(EXISTS { ?fv2 :belongsTo ?ilf2 ; :isUserVisible true } ||
                   EXISTS { ?d2 rdf:type :WindowAction ; :actsOn ?ilf2 ; :reachKind ?rk2 . FILTER(?rk2 != "none") })
            { ?f3 :belongsTo ?ilf2 ; :relatesTo ?m2 .
              FILTER NOT EXISTS { ?f3 :isUserVisible false } }
            UNION { ?a3 rdf:type :WindowAction ; :actsOn ?ilf2 ; :reachKind ?rk3 ; :usesView ?v3 .
                    FILTER(?rk3 != "none") ?v3 :viewFtrRef ?m2 . }
            ?m2 rdf:type :PersistentModel ; :hostModule ?owner2 .
            FILTER(?owner2 != ?targetModule)
            FILTER NOT EXISTS { ?m2 :maintainedBy ?targetModule }
            FILTER NOT EXISTS { ?m2 :foldedInto ?pp2 }
            FILTER(EXISTS { ?fv3 :belongsTo ?m2 ; :isUserVisible true } ||
                   EXISTS { ?d3 rdf:type :WindowAction ; :actsOn ?m2 ; :reachKind ?rk4 . FILTER(?rk4 != "none") })
          } }
        OPTIONAL { SELECT ?m2 ?targetModule (COUNT(DISTINCT ?f4) AS ?exposed)
                          (COUNT(DISTINCT ?ro) AS ?nRt) WHERE {
            ?src2 :hostModule ?targetModule ; :eifField ?f4 .
            ?f4 :belongsTo ?ro .
            { ?ro :foldedInto ?m2 }
            UNION { ?ro :modelName ?anyName . FILTER NOT EXISTS { ?ro :foldedInto ?p9 }
                    BIND(?ro AS ?m2) }
          } GROUP BY ?m2 ?targetModule }
        BIND(1 + COALESCE(?exposed, 0) AS ?det2)
        BIND(IF(COALESCE(?nRt, 0) < 1, 1, ?nRt) AS ?ret2)
        BIND(IF(?det2 <= 19, 0, IF(?det2 <= 50, 1, 2)) AS ?dq2)
        BIND(IF(?ret2 <= 1, 0, IF(?ret2 <= 5, 1, 2)) AS ?rq2)
        BIND(IF(?dq2 + ?rq2 <= 1, 5, IF(?dq2 + ?rq2 <= 2, 7, 10)) AS ?w)
      } }
      BIND("2_EIF" AS ?branch) BIND(CONCAT(STR(?m2),"#EIF#",STR(?targetModule)) AS ?item)
      BIND(:EIF AS ?cls) BIND(?m2 AS ?srcNode) BIND(?ret2out AS ?retv)
      BIND(IF(?ufp=5,"low",IF(?ufp=7,"average","high")) AS ?cx) }

  } }
  BIND(IRI(CONCAT("urn:fn:", ENCODE_FOR_URI(?item))) AS ?fn)
}
