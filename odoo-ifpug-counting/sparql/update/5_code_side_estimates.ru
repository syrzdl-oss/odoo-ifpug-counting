# Code-side estimates: for every module, entry kind (button, route, server action) and
# transaction category, weight = n x p x m, where n is the number of candidates and
# p, m are the calibrated parameters (:pValue, :mValue).
PREFIX : <http://example.org/odoo-ifpug#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

DELETE WHERE { GRAPH <urn:functions> { ?g rdf:type :EstimatedFunctionGroup ; ?p ?o } } ;

INSERT {
  GRAPH <urn:functions> {
    ?fn a :EstimatedFunctionGroup ;
        :inModule ?targetModule ;
        :byRule ?rule ;
        :entryKind ?kind ;
        :txCategory ?cat ;
        :nCandidates ?n ;
        :weight ?ufp ;
        :countingSide "parameterized" .
  }
}
WHERE {
  VALUES ?targetModule {
    :module_crm :module_point_of_sale :module_pos_restaurant :module_sale_management
    :module_contacts :module_mass_mailing :module_survey :module_marketing_card
    :module_website_event :module_mass_mailing_sms :module_stock :module_mrp
    :module_purchase :module_maintenance :module_repair :module_hr
    :module_fleet :module_hr_holidays :module_hr_recruitment :module_hr_attendance
    :module_hr_expense :module_hr_skills :module_lunch :module_account
    :module_website :module_website_sale :module_website_slides :module_im_livechat
    :module_website_hr_recruitment :module_project :module_mail :module_data_recycle
    :module_calendar :module_project_todo
  }
  { SELECT ?targetModule ?kind ?cat ?ufp ?n WHERE {

    { SELECT ?targetModule ?kind (COUNT(*) AS ?n) WHERE {
        { ?x rdf:type :ObjectButton ; :onModel ?bm .
          ?bm rdf:type :PersistentModel ; :hostModule ?hmB .
          OPTIONAL { ?x :declaredIn ?dmB }
          BIND(COALESCE(?dmB, ?hmB) AS ?targetModule)
          BIND("button" AS ?kind) }
        UNION { ?x rdf:type :HttpRoute ; :inModule ?targetModule . BIND("route" AS ?kind) }
        UNION { ?x rdf:type :ServerAction ; :srvState "code" ; :actsOn ?sm .
                ?sm :hostModule ?hmS . OPTIONAL { ?x :declaredIn ?dmS }
                BIND(COALESCE(?dmS, ?hmS) AS ?targetModule) BIND("server" AS ?kind) }
      } GROUP BY ?targetModule ?kind }
    ?sp rdf:type :CalibrationStat ; :entryKind ?kind ; :txCategory ?cat ;
        :pValue ?p ; :mValue ?m .
    BIND(?n * ?p * ?m AS ?ufp)
  } }
  BIND(CONCAT("8_", ?kind, "_", ?cat) AS ?rule)
  BIND(IRI(CONCAT("urn:fn:est:", STRAFTER(STR(?targetModule), "#"), ":", ?kind, ":", ?cat)) AS ?fn)
}
