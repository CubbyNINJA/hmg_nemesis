/* Host stand-in for the bionic header. shim.c implements both. */
#ifndef NEMESIS_TEST_SYSTEM_PROPERTIES_H
#define NEMESIS_TEST_SYSTEM_PROPERTIES_H

int __system_property_get(const char *name, char *value);
int __system_property_set(const char *name, const char *value);

#endif
