---
tags: [tech, containers, docker, devops]
---
# Docker and Container Technology

## What Containers Do

Containers package an application with all of its dependencies, configuration, and runtime into
a single portable unit. Unlike virtual machines, containers share the host operating system kernel
but are isolated using Linux namespaces and cgroups. This makes containers faster to start and
more efficient in memory than full virtual machine images.

## Images and Layers

A Docker image is a read-only template built from a series of layers. Each instruction in a
Dockerfile creates a new immutable layer on top of the previous one. Layers are cached and
shared across images, reducing storage space and network transfer time when pulling images.
The union filesystem combines all layers into a single coherent filesystem view.

## Dockerfile Best Practices

Order Dockerfile instructions from least to most frequently changed to maximize layer caching.
Install system packages before copying application code. Combine RUN commands with && to
minimize the number of layers. Use multi-stage builds to separate the build environment from
the production runtime image, keeping the final image small.

## Networking and Volumes

Containers are connected via virtual networks. The bridge network is the default for standalone
containers. Named volumes persist data beyond the lifecycle of a container. Bind mounts map a
host directory into a container and are useful for development workflows where source code
changes should be reflected immediately without rebuilding the image.

## Docker Compose

Docker Compose defines multi-container applications in a YAML file. Services, networks, and
volumes are declared together and started with a single command. Compose is ideal for local
development environments that require a database, cache, and application server together.
