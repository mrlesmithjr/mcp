---
tags: [tech, databases, sql, nosql]
---
# Databases: Relational and Non-Relational

## Relational Databases

Relational databases store data in tables with rows and columns, enforcing a predefined schema.
SQL (Structured Query Language) is the standard language for querying and manipulating relational
data. Primary keys uniquely identify rows; foreign keys express relationships between tables.
Normalization reduces data redundancy by decomposing tables into minimal, non-redundant forms.

## Transactions and ACID

ACID properties guarantee reliable transaction processing: Atomicity ensures all operations in
a transaction succeed or all are rolled back. Consistency ensures the database transitions
between valid states. Isolation prevents concurrent transactions from observing each other's
partial results. Durability ensures committed transactions survive crashes through write-ahead logging.

## NoSQL Document Stores

Document databases store semi-structured data as JSON or BSON documents. Documents in the same
collection can have different fields, making schema evolution easier than in relational systems.
MongoDB, CouchDB, and Firestore are examples. Denormalization is common: embedding related data
in a single document avoids costly joins at query time.

## Key-Value and Wide-Column Stores

Key-value stores map arbitrary keys to opaque values with minimal structure. Redis is an in-memory
key-value store used for caching, session storage, and pub-sub messaging. Cassandra is a distributed
wide-column store optimized for high-volume time-series and append-heavy workloads with no single
point of failure.

## Vector Databases

Vector databases index high-dimensional embedding vectors produced by machine learning models and
support approximate nearest neighbor (ANN) search. They power semantic search, recommendation
systems, and retrieval-augmented generation pipelines. Specialized ANN indexes such as HNSW
trade recall for query speed.
