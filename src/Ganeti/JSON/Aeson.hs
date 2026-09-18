{-# LANGUAGE FlexibleInstances, TypeSynonymInstances #-}
{-# OPTIONS_GHC -fno-warn-orphans #-}

{-| Aeson-based JSON serialisation mirroring Ganeti's 'Text.JSON' semantics.

This module provides the fast, aeson-based encoding/decoding path used on
performance-critical serialisation (most notably wconfd's config handling),
while keeping byte-for-byte compatibility with the existing 'Text.JSON'
representation.

The crucial difference from plain aeson is how numbers are decoded. The
legacy 'Text.JSON' library represents every JSON number as a @Rational@
('JSRational'), keeping full precision. Aeson instead uses a 'Scientific'
with a bounded exponent and so loses precision on large exponents. To
preserve the exact values Ganeti reads and writes, this module decodes JSON
numbers back into the same @Rational@ values that 'Text.JSON' would produce,
by re-parsing the number's decimal text. The aeson encoding path is used
directly, since it is already the fast, bytestring-based encoder.

-}

{-

Copyright (C) 2026 Ganeti Project Contributors.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

1. Redistributions of source code must retain the above copyright notice,
this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
notice, this list of conditions and the following disclaimer in the
documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS
IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

-}

module Ganeti.JSON.Aeson
  ( jsValueToAeson
  , aesonToJSValue
  ) where

import qualified Data.Aeson as A
import qualified Data.Aeson.Key as K
import qualified Data.Aeson.KeyMap as KM
import qualified Data.Map as Map
import Data.Ratio (denominator, numerator)
import Data.Scientific (Scientific)
import qualified Data.Text as T
import qualified Data.Vector as V
import Text.Read (readMaybe)

import Text.JSON.Types (JSValue(..), JSObject, toJSObject, fromJSObject,
                        toJSString, fromJSString)

import Ganeti.JSON (GenericContainer(..), HasStringRepr(..))

-- | Converts a legacy 'JSValue' into an aeson 'A.Value'.
--
-- This is a structural, total conversion. Numbers keep their exact value:
-- whole numbers stay integers, fractional values become the corresponding
-- floating point number, matching how 'Text.JSON' serialises them.
jsValueToAeson :: JSValue -> A.Value
jsValueToAeson JSNull = A.Null
jsValueToAeson (JSBool b) = A.Bool b
jsValueToAeson (JSRational _ r) = rationalToAeson r
jsValueToAeson (JSString s) = A.String (T.pack $ fromJSString s)
jsValueToAeson (JSArray xs) = A.Array (V.fromList $ map jsValueToAeson xs)
jsValueToAeson (JSObject o) =
  A.Object . KM.fromList
  $ map (\(k, v) -> (K.fromString k, jsValueToAeson v)) (fromJSObject o)

-- | Converts an aeson 'A.Value' back into a legacy 'JSValue'.
--
-- Numbers are decoded to a 'Rational' by re-parsing their exact decimal
-- text, so that no precision is lost compared to 'Text.JSON'.
aesonToJSValue :: A.Value -> JSValue
aesonToJSValue A.Null = JSNull
aesonToJSValue (A.Bool b) = JSBool b
aesonToJSValue (A.Number s) = JSRational False (scientificToRational s)
aesonToJSValue (A.String s) = JSString (toJSString $ T.unpack s)
aesonToJSValue (A.Array xs) = JSArray (map aesonToJSValue (V.toList xs))
aesonToJSValue (A.Object o) =
  JSObject . toJSObject
  $ map (\(k, v) -> (K.toString k, aesonToJSValue v)) (KM.toList o)

-- | Converts a 'Rational' to an aeson numeric value.
rationalToAeson :: Rational -> A.Value
rationalToAeson r
  | denominator r == 1 = A.Number (fromInteger (numerator r))
  | otherwise          = A.Number (realToFrac r)

-- | Decodes a 'Scientific' to a 'Rational' without losing precision.
--
-- 'Data.Scientific' only stores a limited base-10 exponent and would
-- silently truncate very large exponents (e.g. @1e400@). Re-parsing the
-- exact decimal text keeps full precision, matching 'Text.JSON'.
scientificToRational :: Scientific -> Rational
scientificToRational s =
  case readMaybe (show s) of
    Just r  -> r
    -- Fallback for representations 'read' does not accept (e.g. "1e400");
    -- these are outside the values Ganeti handles.
    Nothing -> toRational (fromRational (toRational s) :: Double)


-- * Orphan instances bridging legacy types into the aeson world.
--
-- 'JSValue' is carried inside config objects (e.g. opaque hypervisor
-- parameters), so it needs aeson instances for the whole object graph to be
-- serialisable through aeson.

instance A.ToJSON JSValue where
  toJSON = jsValueToAeson

instance A.FromJSON JSValue where
  parseJSON = return . aesonToJSValue

instance A.ToJSON (JSObject JSValue) where
  toJSON = A.Object . KM.fromList
         . map (\(k, v) -> (K.fromString k, A.toJSON v)) . fromJSObject

instance A.FromJSON (JSObject JSValue) where
  parseJSON = A.withObject "JSObject" $ return . toJSObject
            . map (\(k, v) -> (K.toString k, aesonToJSValue v)) . KM.toList

instance (HasStringRepr a, Ord a, A.ToJSON b) =>
         A.ToJSON (GenericContainer a b) where
  toJSON =
    A.Object . KM.fromList
    . map (\(k, v) -> (K.fromString (toStringRepr k), A.toJSON v))
    . Map.toList . fromContainer

instance (HasStringRepr a, Ord a, A.FromJSON b) =>
         A.FromJSON (GenericContainer a b) where
  parseJSON = A.withObject "GenericContainer" $ \o -> do
    kalist <- mapM (\(k, v) -> do
                      k' <- fromStringRepr (K.toString k)
                      v' <- A.parseJSON v
                      return (k', v')) (KM.toList o)
    return . GenericContainer $ Map.fromList kalist

