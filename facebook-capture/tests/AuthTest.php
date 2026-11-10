<?php

declare(strict_types=1);

use PHPUnit\Framework\TestCase;

final class AuthTest extends TestCase
{
    public function testExtractsTokenFromHeader(): void
    {
        $server = [Auth::HEADER_NAME => 'secret-token'];
        $this->assertSame('secret-token', Auth::extractToken($server, [], []));
    }

    public function testExtractsTokenFromQueryParam(): void
    {
        $this->assertSame('secret-token', Auth::extractToken([], ['token' => 'secret-token'], []));
    }

    public function testExtractsTokenFromPostParam(): void
    {
        $this->assertSame('secret-token', Auth::extractToken([], [], ['token' => 'secret-token']));
    }

    public function testHeaderTakesPrecedenceOverQueryParam(): void
    {
        $server = [Auth::HEADER_NAME => 'from-header'];
        $this->assertSame('from-header', Auth::extractToken($server, ['token' => 'from-query'], []));
    }

    public function testReturnsNullWhenNoTokenPresentAnywhere(): void
    {
        $this->assertNull(Auth::extractToken([], [], []));
    }

    public function testEmptyStringTokenIsTreatedAsAbsent(): void
    {
        $this->assertNull(Auth::extractToken([Auth::HEADER_NAME => ''], ['token' => ''], []));
    }

    public function testIsValidAcceptsMatchingToken(): void
    {
        $this->assertTrue(Auth::isValid('secret-token', 'secret-token'));
    }

    public function testIsValidRejectsMismatchedToken(): void
    {
        $this->assertFalse(Auth::isValid('wrong-token', 'secret-token'));
    }

    public function testIsValidRejectsNullProvidedToken(): void
    {
        $this->assertFalse(Auth::isValid(null, 'secret-token'));
    }

    public function testIsValidRejectsWhenExpectedTokenIsEmpty(): void
    {
        // An unconfigured server (empty FB_CAPTURE_TOKEN) must never
        // accept requests just because nothing was provided to compare
        // against -- that would turn a missing config value into "no
        // auth required" rather than "nothing can authenticate".
        $this->assertFalse(Auth::isValid('anything', ''));
    }
}
