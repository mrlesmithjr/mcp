---
tags: [tech, kubernetes, containers, orchestration]
---
# Kubernetes Container Orchestration

## Core Concepts

Kubernetes is an open-source platform for automating the deployment, scaling, and management
of containerized workloads. It abstracts the underlying infrastructure and provides a declarative
API to describe the desired state of applications. The control plane continuously reconciles
the actual cluster state toward the declared desired state.

## Pods and Workloads

A Pod is the smallest deployable unit in Kubernetes and contains one or more containers that
share a network namespace and storage volumes. Deployments manage stateless application Pods
and support rolling updates and rollback. StatefulSets manage stateful applications that require
stable network identities and persistent storage, such as databases.

## Services and Networking

Services provide stable DNS names and IP addresses for a set of Pods selected by label selectors.
ClusterIP services are reachable only within the cluster. NodePort and LoadBalancer services expose
applications to external traffic. Ingress controllers route HTTP traffic to services based on
hostname and path rules, enabling virtual hosting of multiple applications on a single IP.

## Scheduling and Resources

The Kubernetes scheduler assigns Pods to Nodes based on resource requests and limits, affinity
and anti-affinity rules, taints, and tolerations. Resource requests inform the scheduler about
minimum CPU and memory needed; resource limits prevent a Pod from consuming more than its share.
Horizontal Pod Autoscalers scale the number of Pod replicas based on observed metrics.

## Storage

PersistentVolumes represent storage resources in the cluster. PersistentVolumeClaims are requests
for storage by Pods. StorageClasses define provisioners that dynamically create PersistentVolumes
on demand. CSI drivers integrate external storage systems from cloud providers and on-premises arrays.
