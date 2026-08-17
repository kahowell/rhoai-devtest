BASE=$(dirname $0)
cd $BASE
oc apply -f gatewayclass.yaml
oc apply -f dsci.yaml
oc apply -f kuadrant.yaml
oc apply -f dsc.yaml
./authorino.sh
./maas-gateway.sh
./postgres.sh
oc apply -f ai-gateway.yaml
oc apply -f llmisvc.yaml
oc apply -f coo-uiplugins.yaml
