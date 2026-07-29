---
tags: [tech, networking, tcp-ip, dns]
---
# Networking Fundamentals

## TCP/IP Model

The TCP/IP model organizes network communication into four layers: Link, Internet, Transport,
and Application. The Internet layer handles addressing and routing of packets between networks
using IP addresses. The Transport layer provides reliable delivery via TCP or fast but unreliable
delivery via UDP. The Application layer contains protocols like HTTP, DNS, SMTP, and SSH.

## IP Addressing and Subnets

IPv4 addresses are 32-bit numbers written in dotted-decimal notation. A subnet mask defines which
bits represent the network and which represent the host. CIDR notation expresses subnets as
address/prefix-length, e.g. 192.168.1.0/24. Private address ranges (RFC 1918) are not routable
on the public internet and require NAT to access external services.

## DNS Resolution

The Domain Name System maps human-readable hostnames to IP addresses. A recursive resolver
queries root nameservers, then top-level domain (TLD) nameservers, then authoritative nameservers
to resolve a name. Responses are cached according to the TTL value to reduce query load.
Common record types: A (IPv4), AAAA (IPv6), CNAME (alias), MX (mail), TXT (verification).

## Routing and BGP

Routers forward packets based on a routing table that maps destination prefixes to next-hop
addresses. Interior gateway protocols like OSPF find shortest paths within an autonomous system.
BGP (Border Gateway Protocol) is the exterior gateway protocol that connects autonomous systems
and forms the routing backbone of the global internet.

## TLS and Certificate Authority

TLS encrypts communication between clients and servers using asymmetric key exchange to establish
a symmetric session key. Certificates are signed by a Certificate Authority (CA) and bind a
public key to a domain name. Certificate transparency logs allow public auditing of issued certs.
ACME automates certificate issuance from Let's Encrypt.
