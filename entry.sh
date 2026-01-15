# local args
kube_ns="energy-promv4"
kube_pod="api-7798c4bdf7-j6tdp"

# remote args
dump_type="mini"
dump_pid="1"
dump_dir="/dumps"

# we transfert the args with cat 
{ 
  echo "dump_type=\"${dump_type}\""
  echo "dump_pid=\"${dump_pid}\""
  echo "dump_dir=\"${dump_dir}\""
  cat remote.sh
} | kubectl exec -n ${kube_ns} -i ${kube_pod} -- bash

# Get the real path
real_path=$(kubectl exec -n "${kube_ns}" "${kube_pod}" -- readlink -f "/$dump_dir/latest_dump")

# get the filename only for local file
filename=$(basename "${real_path}")

# Copy the actual file
kubectl cp "${kube_ns}/${kube_pod}:${real_path}" ./"${filename}"
