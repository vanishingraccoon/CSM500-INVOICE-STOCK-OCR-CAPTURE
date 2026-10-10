package com.invoicecapture.domain.pairing

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingCodesTest {

    private fun ok(raw: String): PairingQr {
        val parsed = PairingCodes.parseQr(raw)
        assertTrue("expected Ok, got $parsed", parsed is QrParse.Ok)
        return (parsed as QrParse.Ok).qr
    }

    private fun invalid(raw: String): String {
        val parsed = PairingCodes.parseQr(raw)
        assertTrue("expected Invalid, got $parsed", parsed is QrParse.Invalid)
        return (parsed as QrParse.Invalid).reason
    }

    @Test
    fun contractExampleParses() {
        val qr = ok("""{"v": 1, "server": "http://192.168.1.20:5000", "pair_code": "K7Q2M9", "shop": "eastgate"}""")
        assertEquals(PairingQr("http://192.168.1.20:5000", "K7Q2M9", "eastgate"), qr)
    }

    @Test
    fun whitespaceAroundTheJsonIsIgnored() {
        assertEquals("K7Q2M9", ok("\n  {\"v\":1,\"server\":\"http://192.168.1.20:5000/\",\"pair_code\":\"K7Q2M9\"}  \n").pairCode)
    }

    @Test
    fun shopIsOptional() {
        assertNull(ok("""{"v": 1, "server": "http://10.0.0.5:5000", "pair_code": "ABCDEF"}""").shop)
    }

    @Test
    fun newerVersionAsksForAnUpdate() {
        assertTrue(invalid("""{"v": 2, "server": "http://192.168.1.20:5000", "pair_code": "K7Q2M9"}""").contains("newer"))
    }

    @Test
    fun missingOrBadFieldsAreRejected() {
        invalid("""{"server": "http://192.168.1.20:5000", "pair_code": "K7Q2M9"}""")
        invalid("""{"v": 1, "pair_code": "K7Q2M9"}""")
        invalid("""{"v": 1, "server": "http://192.168.1.20:5000"}""")
        invalid("""{"v": 1, "server": "ftp://192.168.1.20", "pair_code": "K7Q2M9"}""")
        invalid("""{"v": 1, "server": "http://192.168.1.20:5000", "pair_code": "K7Q2M"}""")
        invalid("""{"v": 1, "server": "http://192.168.1.20:5000", "pair_code": "K7Q2M0"}""")
        invalid("""{"v": 1, "server": "http://192.168.1.20:5000", "pair_code": "KOQ2M9"}""")
    }

    @Test
    fun somethingElseEntirelyIsRejected() {
        invalid("https://example.com/menu")
        invalid("")
        invalid("[1, 2, 3]")
    }

    @Test
    fun codesAreNormalisedAndChecked() {
        assertEquals("K7Q2M9", PairingCodes.normaliseCode(" k7q-2m9 "))
        assertTrue(PairingCodes.isValidCode("K7Q2M9"))
        assertFalse(PairingCodes.isValidCode("K7Q2MI")) // I is not in the alphabet
        assertFalse(PairingCodes.isValidCode("K7Q2M91"))
    }

    @Test
    fun typedServerAddressesAreNormalised() {
        assertEquals("http://192.168.1.20:5000", PairingCodes.normaliseServer("192.168.1.20:5000"))
        assertEquals("http://192.168.1.20:5000", PairingCodes.normaliseServer(" http://192.168.1.20:5000/ "))
        assertEquals("http://laptop.local:5000", PairingCodes.normaliseServer("HTTP://Laptop.local:5000"))
        assertEquals("https://invoices.example.com", PairingCodes.normaliseServer("https://invoices.example.com"))
    }

    @Test
    fun unusableServerAddressesAreRejected() {
        assertNull(PairingCodes.normaliseServer(""))
        assertNull(PairingCodes.normaliseServer("http://"))
        assertNull(PairingCodes.normaliseServer("ftp://192.168.1.20"))
        assertNull(PairingCodes.normaliseServer("http://192.168.1.20:5000/?x=1"))
    }
}
