# Derives :maintainedBy. An application maintains a table hosted by another module
# when it declares a reachable window action whose selected form view on that table
# allows creating records. The table is then an ILF of that application, not an EIF.
PREFIX : <http://example.org/odoo-ifpug#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
INSERT DATA { :maintainedBy a owl:ObjectProperty . } ;
INSERT { ?t :maintainedBy ?x }
WHERE {
  ?x rdf:type :Module ; :isApplication true .
  ?t rdf:type :PersistentModel ; :hostModule ?h . FILTER(?h != ?x)
  FILTER NOT EXISTS { ?t :foldedInto ?p }
  ?a rdf:type :WindowAction ; :actsOn ?t ; :declaredIn ?x ; :reachKind ?k ; :usesView ?v .
  FILTER(?k != "none") ?v :viewType "form" ; :gateCreate true .
}
